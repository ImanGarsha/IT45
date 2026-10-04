"""Two-leg executor: maker-first, taker fallback, hedge-imbalance repair.

open/close a pair = run both legs concurrently:
  for each leg, up to `maker_attempts` times: post-only at the touch (best bid for a buy,
  best ask for a sell), wait `reprice_s`, cancel, re-price. Remaining size then goes IOC at
  touch +/- max_slippage_bp if `taker_fallback`.
  An order sent without a reply ('unknown') ends that leg at once: nothing more is sent until
the engine has asked the venue what became of it (Engine._reconcile_unknown).
Once one leg is complete the other gets `imbalance_timeout_s` to finish before it is pushed
  straight to IOC.
After both legs: if filled notionals differ by more than imbalance_pct, the lighter leg gets
one more IOC; if that fails the heavier leg is trimmed (reduce-only IOC) to match. A pair is
never left holding a lone leg silently -- every outcome is written to the audit table.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class Leg:
    venue: str
    symbol: str
    kind: str
    side: str            # buy | sell
    size: float          # base units, > 0
    reduce_only: bool = False


@dataclass
class LegResult:
    leg: Leg
    filled: float = 0.0
    notional: float = 0.0
    fees: float = 0.0
    error: str | None = None

    @property
    def avg_px(self):
        return self.notional / self.filled if self.filled else None


class Executor:
    def __init__(self, engine):
        self.e = engine

    def _x(self):
        return self.e.cfg()["exec"]

    async def _submit(self, leg: Leg, size: float, price: float, post_only: bool, pair_id: str | None):
        br = self.e.brokers[leg.venue]
        book = await br.book(leg.symbol, leg.kind)
        mid = book.mid if book else None
        d = self.e.guard.check(
            venue=leg.venue, mode=br.mode, symbol=leg.symbol, side=leg.side,
            notional=size * (mid or price), price=price, book_mid=mid,   # caps measured at mid
            book_age_s=(time.time() - book.ts) if book else None, reduce_only=leg.reduce_only,
            venue_gross=await self.e.venue_gross(leg.venue), book_gross=await self.e.book_gross())
        if not d.ok:
            return None, d.reason
        ack = await br.place(symbol=leg.symbol, kind=leg.kind, side=leg.side, size=size, price=price,
                             post_only=post_only, reduce_only=leg.reduce_only, pair_id=pair_id)
        if ack.status == "unknown" or (ack.status == "rejected" and not (ack.error or "").startswith("post-only")):
            self.e.api_error(leg.venue, ack.error)
        else:
            self.e.api_ok(leg.venue)
        return ack, ack.error

    async def _wait(self, leg: Leg, oid: str, seconds: float, deadline: float | None):
        br = self.e.brokers[leg.venue]
        end = time.time() + seconds
        while time.time() < end and (deadline is None or time.time() < deadline):
            await asyncio.sleep(1.0)
            a = await br.status(oid)
            if a.status in ("filled", "canceled", "rejected"):
                return a
        a = await br.cancel(oid)
        return a

    async def run_leg(self, leg: Leg, pair_id: str | None, other_done: asyncio.Event | None,
                      done: asyncio.Event | None) -> LegResult:
        x = self._x()
        res = LegResult(leg)
        remaining = leg.size
        br = self.e.brokers[leg.venue]
        deadline = None
        try:
            for _ in range(int(x["maker_attempts"])):
                if remaining <= leg.size * 1e-6:
                    break
                if other_done is not None and other_done.is_set():
                    deadline = deadline or time.time() + float(self.e.cfg()["brakes"]["imbalance_timeout_s"])
                    if time.time() >= deadline:
                        break
                book = await br.book(leg.symbol, leg.kind)
                if not book:
                    res.error = "no book"
                    break
                px = book.bid if leg.side == "buy" else book.ask
                ack, err = await self._submit(leg, remaining, px, True, pair_id)
                if ack is None:
                    res.error = err
                    return res                       # guard refused: do not fall back to taker
                if ack.status == "unknown":
                    # sent but no reply: the order may be live. Sending more could double the leg,
                    # so stop here; the engine reconciles the order and the brakes the positions.
                    res.error = ack.error
                    return res
                if ack.status == "rejected":
                    await asyncio.sleep(1.0)
                    continue
                a = ack if ack.status == "filled" else await self._wait(leg, ack.order_id,
                                                                       float(x["reprice_s"]), deadline)
                if a.filled:
                    res.filled += a.filled
                    res.notional += a.filled * (a.avg_px or px)
                    res.fees += a.fee
                    remaining -= a.filled
            if remaining > leg.size * 1e-6 and x["taker_fallback"]:
                book = await br.book(leg.symbol, leg.kind)
                if book:
                    slip = float(x["max_slippage_bp"]) / 1e4
                    px = book.ask * (1 + slip) if leg.side == "buy" else book.bid * (1 - slip)
                    ack, err = await self._submit(leg, remaining, px, False, pair_id)
                    if ack is None:
                        res.error = err
                    elif ack.filled:
                        res.filled += ack.filled
                        res.notional += ack.filled * ack.avg_px
                        res.fees += ack.fee
                        remaining -= ack.filled
                    elif ack.error:
                        res.error = ack.error
        finally:
            if done is not None:
                done.set()
        return res

    async def preflight(self, legs: list[Leg]) -> str | None:
        """Run the guard on every leg BEFORE sending anything. Both legs of a new pair also count
        against the same venue cap when they share a venue (spot carry)."""
        extra: dict[str, float] = {}
        total = 0.0
        for leg in legs:
            br = self.e.brokers[leg.venue]
            book = await br.book(leg.symbol, leg.kind)
            if not book:
                return f"{leg.venue} {leg.symbol}: no book"
            n = leg.size * book.mid
            d = self.e.guard.check(
                venue=leg.venue, mode=br.mode, symbol=leg.symbol, side=leg.side, notional=n,
                price=book.mid, book_mid=book.mid, book_age_s=time.time() - book.ts,
                reduce_only=leg.reduce_only,
                venue_gross=await self.e.venue_gross(leg.venue) + extra.get(leg.venue, 0.0),
                book_gross=await self.e.book_gross() + total, record=False)
            if not d.ok:
                return f"{leg.venue}: {d.reason}"
            if not leg.reduce_only:
                extra[leg.venue] = extra.get(leg.venue, 0.0) + n
                total += n
        return None

    async def run_pair(self, legs: list[Leg], pair_id: str | None) -> list[LegResult]:
        why = await self.preflight(legs)
        if why:
            self.e.db.audit("pair_preflight_refused", "", legs[0].symbol, False, {"pair": pair_id, "reason": why})
            return [LegResult(legs[0], error=why), LegResult(legs[1], error=why)]
        a_done, b_done = asyncio.Event(), asyncio.Event()
        ra, rb = await asyncio.gather(self.run_leg(legs[0], pair_id, b_done, a_done),
                                      self.run_leg(legs[1], pair_id, a_done, b_done))
        await self._repair(ra, rb, pair_id)
        return [ra, rb]

    async def _repair(self, ra: LegResult, rb: LegResult, pair_id):
        """Make the two legs match in notional (within imbalance_pct).

        1. the lighter leg gets one IOC for the missing notional (same side, same reduce-only
           flag: when opening it adds, when closing it closes more);
        2. opening only: if still lopsided, the heavier leg is trimmed with a reduce-only IOC.
        Whatever remains is caught by the imbalance brake on the next risk pass.
        """
        tol = float(self.e.cfg()["brakes"]["imbalance_pct"])

        def lopsided():
            big = max(ra.notional, rb.notional)
            return big > 0 and abs(ra.notional - rb.notional) / big > tol

        if not lopsided():
            return
        light, heavy = (ra, rb) if ra.notional < rb.notional else (rb, ra)
        br = self.e.brokers[light.leg.venue]
        b = await br.book(light.leg.symbol, light.leg.kind)
        if b:
            missing = (heavy.notional - light.notional) / b.mid
            top = await self.ioc(Leg(light.leg.venue, light.leg.symbol, light.leg.kind, light.leg.side,
                                     missing, light.leg.reduce_only), pair_id)
            light.filled += top.filled
            light.notional += top.notional
            light.fees += top.fees
        if not lopsided():
            self.e.db.audit("imbalance_repaired", light.leg.venue, light.leg.symbol, True, {"pair": pair_id})
            return
        if heavy.leg.reduce_only:
            self.e.db.audit("imbalance_open", heavy.leg.venue, heavy.leg.symbol, False,
                            {"pair": pair_id, "note": "close left legs unequal; imbalance brake will retry"})
            return
        excess = (heavy.notional - light.notional) / (heavy.avg_px or 1)
        back = "sell" if heavy.leg.side == "buy" else "buy"
        r = await self.ioc(Leg(heavy.leg.venue, heavy.leg.symbol, heavy.leg.kind, back, excess, True), pair_id)
        heavy.filled -= r.filled
        heavy.notional -= r.filled * (heavy.avg_px or 0)
        self.e.db.audit("imbalance_trimmed", heavy.leg.venue, heavy.leg.symbol, r.filled > 0,
                        {"pair": pair_id, "size": excess, "filled": r.filled, "error": r.error})

    async def ioc(self, leg: Leg, pair_id: str | None) -> LegResult:
        """Immediate taker order (closing, repairs, kill switch)."""
        res = LegResult(leg)
        br = self.e.brokers[leg.venue]
        book = await br.book(leg.symbol, leg.kind)
        if not book:
            res.error = "no book"
            return res
        slip = float(self._x()["max_slippage_bp"]) / 1e4
        px = book.ask * (1 + slip) if leg.side == "buy" else book.bid * (1 - slip)
        ack, err = await self._submit(leg, leg.size, px, False, pair_id)
        if ack is None:
            res.error = err
        elif ack.filled:
            res.filled, res.notional, res.fees = ack.filled, ack.filled * ack.avg_px, ack.fee
        else:
            res.error = ack.error
        return res
