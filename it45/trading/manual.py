"""Manual trading: orders and hedged pairs you place yourself from the dashboard.

Everything here goes through the same Executor -> Guard path as the strategies, so the kill
switch, venue modes (off / paper / testnet; no mainnet before M3), halted venues, price sanity
and size caps apply to manual orders exactly as to automatic ones.

Order types
  market     IOC at the touch +/- max_slippage_bp: fills now (taker) or not at all
  limit      if the limit crosses the book it fills now as taker (up to the limit);
             otherwise it rests as a maker order until filled or cancelled
  post_only  rests as maker; rejected if it would cross
Manual hedged pairs use the same maker-first two-leg executor as the strategies and are
recorded as strategy "manual", so the imbalance and liquidation brakes watch them too.
"""
from __future__ import annotations

import time

from .db import new_id
from .executor import Leg


class ManualError(ValueError):
    pass


class Manual:
    def __init__(self, engine):
        self.e = engine
        self.watching: dict[tuple[str, str, str], float] = {}   # ticket markets -> last poll time

    def watched(self, ttl_s: float = 30.0) -> list[tuple[str, str, str]]:
        """Markets an open New order page is showing (polled within ttl_s): streamed live."""
        now = time.time()
        self.watching = {k: t for k, t in self.watching.items() if now - t < ttl_s}
        return list(self.watching)

    async def ticket(self, venue: str, symbol: str, kind: str = "perp") -> dict:
        """Everything the order ticket shows for one market."""
        br = self._broker(venue)
        self.watching[(venue, symbol, kind)] = time.time()
        book = await br.book(symbol, kind)
        pos = await self.e.position(venue, symbol, kind)
        acc = await br.account()
        q = next((x for x in self.e.feed.quotes.get(venue, []) if x.symbol == symbol and x.kind == kind), None)
        return {
            "venue": venue, "symbol": symbol, "kind": kind, "mode": br.mode,
            "book": None if not book else {"bid": book.bid, "ask": book.ask, "mid": book.mid,
                                           "age_s": time.time() - book.ts},
            "position": pos.to_dict() if pos else None,
            "account": acc,
            "maker_fee": q.maker_fee if q else None, "taker_fee": q.taker_fee if q else None,
            "funding_apr": q.funding_apr if q else None,
            "max_leverage": q.max_leverage if q else None,
            "caps": self.e.cfg()["caps"], "leverage": self.e.cfg()["leverage"],
        }

    def _broker(self, venue):
        mode = self.e.cfg()["venue_modes"].get(venue, "off")
        if venue not in self.e.brokers or mode == "off":
            raise ManualError(f"{venue} is off. Set it to paper or testnet under Risk, Limits.")
        return self.e.brokers[venue]

    async def order(self, *, venue: str, symbol: str, kind: str, side: str, order_type: str,
                    size: float | None = None, usd: float | None = None, price: float | None = None,
                    reduce_only: bool = False) -> dict:
        if side not in ("buy", "sell"):
            raise ManualError("side must be buy or sell")
        if order_type not in ("market", "limit", "post_only"):
            raise ManualError("type must be market, limit or post_only")
        if kind == "spot" and side == "sell" and not reduce_only:
            reduce_only = True     # spot can only be sold if held
        br = self._broker(venue)
        book = await br.book(symbol, kind)
        if not book:
            raise ManualError(f"no live book for {symbol} on {venue}")
        if size is None:
            if not usd or usd <= 0:
                raise ManualError("give a size or a USD amount")
            size = usd / book.mid
        if size <= 0:
            raise ManualError("size must be positive")
        leg = Leg(venue, symbol, kind, side, size, reduce_only)
        x = self.e.executor
        self.e.db.audit("manual_order", venue, symbol, True, {"side": side, "type": order_type, "size": size,
                                                              "price": price, "reduce_only": reduce_only})
        if order_type == "market":
            r = await x.ioc(leg, None)
            return {"status": "filled" if r.filled else "not filled", "filled": r.filled, "avg_px": r.avg_px,
                    "fees": r.fees, "error": r.error}
        if not price or price <= 0:
            raise ManualError("a limit order needs a price")
        crosses = (side == "buy" and price >= book.ask) or (side == "sell" and price <= book.bid)
        if order_type == "post_only" and crosses:
            raise ManualError(f"post-only {side} at {price:g} would cross the book "
                              f"(bid {book.bid:g} / ask {book.ask:g})")
        ack, err = await x._submit(leg, size, price, post_only=not crosses, pair_id=None)
        if ack is None:
            return {"status": "refused", "error": err}
        return {"status": ack.status, "order_id": ack.order_id, "filled": ack.filled, "avg_px": ack.avg_px,
                "fees": ack.fee, "error": ack.error,
                "note": "crossed the book: filled as taker" if crosses else "resting as maker"}

    async def pair(self, *, symbol: str, long_venue: str, long_kind: str, short_venue: str, usd: float) -> dict:
        """Open a hedged pair now (no persistence test: you are deciding)."""
        if long_venue == short_venue and long_kind == "perp":
            raise ManualError("long and short legs must differ")
        for v in (long_venue, short_venue):
            self._broker(v)
        taken = {(p["long_venue"], p["symbol"], p["long_kind"]) for p in self.e.db.open_pairs()} | \
                {(p["short_venue"], p["symbol"], "perp") for p in self.e.db.open_pairs()}
        if (long_venue, symbol, long_kind) in taken or (short_venue, symbol, "perp") in taken:
            raise ManualError("one of these legs already belongs to an open pair")
        mid = self.e.paper_or_live_mid(long_venue, symbol, long_kind)
        if not mid:
            b = await self.e.brokers[long_venue].book(symbol, long_kind)
            mid = b.mid if b else None
        if not mid:
            raise ManualError(f"no live price for {symbol} on {long_venue}")
        size = usd / mid
        legs = [Leg(long_venue, symbol, long_kind, "buy", size), Leg(short_venue, symbol, "perp", "sell", size)]
        why = await self.e.executor.preflight(legs)
        if why:
            raise ManualError(why)
        pid = new_id("pair")
        self.e.db.insert_pair({"id": pid, "strategy": "manual", "symbol": symbol, "long_venue": long_venue,
                               "long_kind": long_kind, "short_venue": short_venue, "short_kind": "perp",
                               "target_usd": usd, "status": "opening", "opened_ts": time.time(),
                               "entry_edge_apr": None, "entry_basis_bp": None, "notes": "manual"})
        ra, rb = await self.e.executor.run_pair(legs, pid)
        if ra.filled == 0 and rb.filled == 0:
            self.e.db.update_pair(pid, status="failed", closed_ts=time.time(),
                                  close_reason=f"not opened: {ra.error or rb.error or 'no fills'}")
        else:
            self.e.db.update_pair(pid, status="open",
                                  notes=f"manual: long {ra.filled:.6g} @ {ra.avg_px or 0:.6g}, "
                                        f"short {rb.filled:.6g} @ {rb.avg_px or 0:.6g}; fees ${ra.fees + rb.fees:.4f}")
        return {"pair_id": pid, "long": {"filled": ra.filled, "avg_px": ra.avg_px, "error": ra.error},
                "short": {"filled": rb.filled, "avg_px": rb.avg_px, "error": rb.error}}

    async def close_position(self, venue: str, symbol: str, kind: str, force: bool = False) -> dict:
        """Close one market's whole (netted) position. Refused if a hedged pair owns part of it,
        because closing would leave that pair one-legged; close the pair, or trim with a
        reduce-only order on New order."""
        if not force:
            for p in self.e.db.open_pairs():
                if p["symbol"] == symbol and ((p["long_venue"], p["long_kind"]) == (venue, kind)
                                              or (p["short_venue"], "perp") == (venue, kind)):
                    raise ManualError(f"this position is a leg of open pair {p['id']} ({p['strategy']}). "
                                      "Close the pair under Pairs, or trim it with a reduce-only order on New order.")
        pos = await self.e.position(venue, symbol, kind)
        if not pos or abs(pos.size) < 1e-12:
            raise ManualError("no open position")
        side = "sell" if pos.size > 0 else "buy"
        r = await self.e.executor.ioc(Leg(venue, symbol, kind, side, abs(pos.size), True), None)
        self.e.db.audit("manual_close", venue, symbol, r.filled > 0, {"filled": r.filled, "error": r.error})
        return {"filled": r.filled, "avg_px": r.avg_px, "error": r.error}

    async def cancel(self, order_id: str) -> dict:
        row = self.e.db.q("SELECT venue, status FROM orders WHERE id=?", (order_id,))
        if not row:
            raise ManualError("unknown order")
        if row[0]["status"] != "open":
            raise ManualError(f"order is already {row[0]['status']}")
        a = await self.e.brokers[row[0]["venue"]].cancel(order_id)
        self.e.db.audit("manual_cancel", row[0]["venue"], "", True, {"order": order_id, "status": a.status})
        return {"status": a.status, "filled": a.filled}
