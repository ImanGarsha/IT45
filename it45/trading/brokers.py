"""Brokers: one async interface per venue.

PaperBroker  -- simulated orders against the LIVE quotes of that venue. Fees from the venue's
                (possibly overridden) schedule, funding accrued continuously on perps, margin
                and an isolated-style liquidation estimate per leg. State lives in SQLite.
HyperliquidTestnet -- see hl_testnet.py.

Paper fill model (deliberately conservative, documented in docs/02_paper_model.md):
- quotes: the venue's bid/ask when published, else mid +/- `half_spread_bp`.
- post-only BUY at P is rejected if P >= ask; it fills only once the ask trades BELOW P
  (the market moved through our price), at P, maker fee. SELL mirrors.
- IOC (taker) BUY with limit L fills the whole size at the ask if ask <= L, taker fee, else
  nothing. No depth model: caps keep orders small relative to venue volume.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..models import Quote
from .db import TradeDB, new_id


@dataclass
class Book:
    bid: float
    ask: float
    mid: float
    ts: float


REQUEST_TIMEOUT_S = 10.0     # every venue request: fail cleanly instead of hanging on a dead link


def no_reply(e: BaseException) -> bool:
    """True when an order MAY have reached the venue but no answer came back (read timeout,
    connection dropped mid-request). False when it certainly was not sent (could not connect,
    name lookup failed) or the venue answered with an error. Such orders are recorded as
    'unknown' and reconciled with the venue once it answers again."""
    names = {c.__name__ for c in type(e).__mro__}
    if names & {"ConnectError", "ConnectTimeout", "gaierror", "NameResolutionError"}:
        return False          # never left this computer
    if names & {"HTTPStatusError"}:
        return False          # the venue answered
    return bool(names & {"TimeoutException", "ReadTimeout", "WriteTimeout", "PoolTimeout", "TransportError",
                         "RemoteProtocolError", "ReadError", "WriteError", "ConnectionError", "Timeout",
                         "ChunkedEncodingError", "TimeoutError", "ConnectionResetError", "BrokenPipeError",
                         "IncompleteRead", "ProtocolError"})


@dataclass
class Ack:
    order_id: str
    status: str               # open | filled | canceled | rejected | unknown (sent, no reply)
    filled: float = 0.0
    avg_px: float | None = None
    fee: float = 0.0
    error: str | None = None


@dataclass
class Position:
    venue: str
    symbol: str
    kind: str
    size: float               # signed, base units
    avg_px: float
    mark: float | None = None
    upnl: float = 0.0
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    liq_px: float | None = None
    liq_distance: float | None = None

    @property
    def notional(self) -> float:
        return abs(self.size) * (self.mark or self.avg_px)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["notional"] = self.notional
        return d


class Broker:
    venue: str = ""
    mode: str = ""

    async def book(self, symbol: str, kind: str) -> Book | None: ...
    async def place(self, *, symbol: str, kind: str, side: str, size: float, price: float,
                    post_only: bool, reduce_only: bool = False, pair_id: str | None = None) -> Ack: ...
    async def status(self, order_id: str) -> Ack: ...
    async def cancel(self, order_id: str) -> Ack: ...
    async def positions(self) -> list[Position]: ...
    async def account(self) -> dict: ...
    async def cancel_all(self) -> int: ...

    def tick(self, dt: float) -> None:
        """Called once per feed cycle (paper: fills resting orders, accrues funding)."""


@dataclass
class _Resting:
    id: str
    symbol: str
    kind: str
    side: str
    size: float
    price: float
    reduce_only: bool
    pair_id: str | None
    placed: float = field(default_factory=time.time)
    queue_ahead: float = 0.0    # size resting at our price before us (live stream), base units


class PaperBroker(Broker):
    mode = "paper"

    def __init__(self, venue: str, feed, db: TradeDB, cfg_getter, half_spread_bp: float = 2.0, hub=None):
        self.venue = venue
        self.feed = feed
        self.hub = hub            # StreamHub: live top of book + trade prints (optional)
        self.db = db
        self.cfg = cfg_getter
        self.hs = half_spread_bp / 1e4
        self.resting: dict[str, _Resting] = {}
        self._funding_buf: dict[str, float] = {}
        self._last_flush = time.time()
        # orders left resting by a previous run cannot be honoured: cancel them visibly
        self.db.x("UPDATE orders SET status='canceled', error='canceled: IT45 restarted' "
                  "WHERE venue=? AND mode='paper' AND status='open'", (venue,))
        start = float(self.cfg()["paper_balance_usd"])
        if not self.db.q("SELECT 1 FROM paper_cash WHERE venue=?", (venue,)):
            self.db.x("INSERT INTO paper_cash VALUES (?,?,?)", (venue, start, start))

    # ---------------------------------------------------------------- quotes
    def _quote(self, symbol: str, kind: str) -> Quote | None:
        for q in self.feed.quotes.get(self.venue, []):
            if q.symbol == symbol and q.kind == kind:
                return q
        return None

    def _book(self, symbol: str, kind: str) -> Book | None:
        top = self.hub.top(self.venue, symbol) if (self.hub and kind == "perp") else None
        if top:
            return Book(bid=top.bid, ask=top.ask, mid=(top.bid + top.ask) / 2, ts=top.ts)
        q = self._quote(symbol, kind)
        if not q or not q.px:
            return None
        bid, ask = q.bid, q.ask
        if not (bid and ask and bid < ask):
            bid, ask = q.px * (1 - self.hs), q.px * (1 + self.hs)
        return Book(bid=bid, ask=ask, mid=(bid + ask) / 2, ts=q.ts)

    async def book(self, symbol, kind):
        return self._book(symbol, kind)

    def cached_mid(self, symbol, kind) -> float | None:
        b = self._book(symbol, kind)
        return b.mid if b else None

    # ---------------------------------------------------------------- ledger
    def _pos_row(self, symbol, kind) -> dict:
        r = self.db.q("SELECT * FROM paper_positions WHERE venue=? AND symbol=? AND kind=?",
                      (self.venue, symbol, kind))
        return r[0] if r else {"venue": self.venue, "symbol": symbol, "kind": kind, "size": 0.0,
                               "avg_px": 0.0, "realized": 0.0, "fees": 0.0, "funding": 0.0}

    def _cash(self) -> float:
        return self.db.q("SELECT cash FROM paper_cash WHERE venue=?", (self.venue,))[0]["cash"]

    def _add_cash(self, amt: float) -> None:
        self.db.x("UPDATE paper_cash SET cash=cash+? WHERE venue=?", (amt, self.venue))

    def _fee_rate(self, symbol, kind, maker: bool) -> float:
        q = self._quote(symbol, kind)
        r = (q.maker_fee if maker else q.taker_fee) if q else None
        return float(r or 0.0)

    def _apply_fill(self, symbol, kind, side, size, px, maker: bool, order_id, pair_id) -> float:
        fee = abs(size) * px * self._fee_rate(symbol, kind, maker)
        p = self._pos_row(symbol, kind)
        signed = size if side == "buy" else -size
        old = p["size"]
        new = old + signed
        realized = 0.0
        if kind == "spot":
            self._add_cash(-signed * px)          # pay for buys, receive for sells
            if old == 0 or (old > 0) == (signed > 0):
                p["avg_px"] = (old * p["avg_px"] + signed * px) / new if new else 0.0
            else:
                realized = min(abs(signed), abs(old)) * (px - p["avg_px"]) * (1 if old > 0 else -1)
        else:
            if old == 0 or (old > 0) == (signed > 0):          # opening / adding
                p["avg_px"] = (abs(old) * p["avg_px"] + abs(signed) * px) / abs(new)
            else:                                               # reducing / flipping
                closed = min(abs(signed), abs(old))
                realized = closed * (px - p["avg_px"]) * (1 if old > 0 else -1)
                self._add_cash(realized)
                if abs(signed) > abs(old):
                    p["avg_px"] = px                            # flipped: remainder at fill px
                elif new == 0:
                    p["avg_px"] = 0.0
        self._add_cash(-fee)
        p.update(size=new if abs(new) > 1e-12 else 0.0, realized=p["realized"] + realized,
                 fees=p["fees"] + fee)
        self.db.x("INSERT OR REPLACE INTO paper_positions VALUES (?,?,?,?,?,?,?,?)",
                  (self.venue, symbol, kind, p["size"], p["avg_px"], p["realized"], p["fees"], p["funding"]))
        self.db.add_fill({"ts": time.time(), "order_id": order_id, "venue": self.venue, "mode": "paper",
                          "symbol": symbol, "kind": kind, "side": side, "size": size, "px": px,
                          "fee": fee, "liquidity": "maker" if maker else "taker", "pair_id": pair_id})
        return fee

    # ---------------------------------------------------------------- margin
    def _lev(self) -> float:
        return float(self.cfg()["leverage"])

    def _margin_used(self) -> float:
        lev = self._lev()
        used = 0.0
        for r in self.db.q("SELECT * FROM paper_positions WHERE venue=? AND size!=0", (self.venue,)):
            b = self._book(r["symbol"], r["kind"])
            px = b.mid if b else r["avg_px"]
            used += abs(r["size"]) * px * (1.0 if r["kind"] == "spot" else 1.0 / lev)
        for o in self.resting.values():
            if not o.reduce_only:
                used += o.size * o.price * (1.0 if o.kind == "spot" else 1.0 / lev)
        return used

    def _equity(self) -> float:
        eq = self._cash()
        for r in self.db.q("SELECT * FROM paper_positions WHERE venue=? AND size!=0", (self.venue,)):
            b = self._book(r["symbol"], r["kind"])
            mark = b.mid if b else r["avg_px"]
            eq += r["size"] * mark if r["kind"] == "spot" else r["size"] * (mark - r["avg_px"])
        return eq

    # ---------------------------------------------------------------- orders
    def _record(self, oid, symbol, kind, side, size, price, post_only, reduce_only, pair_id, status, error=None):
        self.db.insert_order({"id": oid, "ts": time.time(), "updated_ts": time.time(), "venue": self.venue,
                              "mode": "paper", "symbol": symbol, "kind": kind, "side": side, "size": size,
                              "limit_px": price, "post_only": int(post_only), "reduce_only": int(reduce_only),
                              "status": status, "pair_id": pair_id, "error": error})

    async def place(self, *, symbol, kind, side, size, price, post_only, reduce_only=False, pair_id=None):
        oid = new_id("P")
        b = self._book(symbol, kind)

        def reject(msg):
            self._record(oid, symbol, kind, side, size, price, post_only, reduce_only, pair_id, "rejected", msg)
            return Ack(oid, "rejected", error=msg)

        if b is None:
            return reject("no live quote")
        if size <= 0:
            return reject("size must be positive")
        cur = self._pos_row(symbol, kind)["size"]
        if reduce_only:
            if cur == 0 or (cur > 0) == (side == "buy"):
                return reject("reduce-only order would increase the position")
            size = min(size, abs(cur))
        if kind == "spot" and side == "sell" and size > max(cur, 0) + 1e-12:
            return reject("cannot sell more spot than held (no spot shorting)")
        if not reduce_only:
            need = size * price * (1.0 if kind == "spot" else 1.0 / self._lev())
            free = self._equity() - self._margin_used()
            if need > free + 1e-9:
                return reject(f"insufficient paper collateral: need ${need:,.2f}, free ${free:,.2f}")
        if post_only:
            if (side == "buy" and price >= b.ask) or (side == "sell" and price <= b.bid):
                return reject("post-only would cross the book")
            # queue position: joining an existing level puts us behind the size already there
            queue = 0.0
            top = self.hub.top(self.venue, symbol) if (self.hub and kind == "perp") else None
            if top:
                if side == "buy" and abs(price - top.bid) <= 1e-9 * price:
                    queue = top.bid_sz
                elif side == "sell" and abs(price - top.ask) <= 1e-9 * price:
                    queue = top.ask_sz
            self.resting[oid] = _Resting(oid, symbol, kind, side, size, price, reduce_only, pair_id,
                                         queue_ahead=queue)
            self._record(oid, symbol, kind, side, size, price, True, reduce_only, pair_id, "open")
            return Ack(oid, "open")
        # IOC taker
        touch = b.ask if side == "buy" else b.bid
        if (side == "buy" and touch > price) or (side == "sell" and touch < price):
            self._record(oid, symbol, kind, side, size, price, False, reduce_only, pair_id, "canceled",
                         "IOC: touch beyond limit, nothing filled")
            return Ack(oid, "canceled", error="IOC: touch beyond limit")
        self._record(oid, symbol, kind, side, size, price, False, reduce_only, pair_id, "open")
        fee = self._apply_fill(symbol, kind, side, size, touch, False, oid, pair_id)
        self.db.update_order(oid, status="filled", filled=size, avg_px=touch, fee=fee)
        return Ack(oid, "filled", size, touch, fee)

    async def status(self, order_id):
        r = self.db.q("SELECT * FROM orders WHERE id=?", (order_id,))
        if not r:
            return Ack(order_id, "rejected", error="unknown order")
        r = r[0]
        return Ack(order_id, r["status"], r["filled"] or 0.0, r["avg_px"], r["fee"] or 0.0, r["error"])

    async def cancel(self, order_id):
        o = self.resting.pop(order_id, None)
        if o:
            self.db.update_order(order_id, status="canceled")
        return await self.status(order_id)

    async def cancel_all(self) -> int:
        n = len(self.resting)
        for oid in list(self.resting):
            await self.cancel(oid)
        return n

    # ---------------------------------------------------------------- views
    async def positions(self):
        lev = self._lev()
        out = []
        for r in self.db.q("SELECT * FROM paper_positions WHERE venue=? AND size!=0", (self.venue,)):
            b = self._book(r["symbol"], r["kind"])
            mark = b.mid if b else None
            p = Position(self.venue, r["symbol"], r["kind"], r["size"], r["avg_px"], mark,
                         realized=r["realized"], fees=r["fees"], funding=r["funding"])
            if mark:
                p.upnl = r["size"] * (mark - r["avg_px"])
                if r["kind"] == "perp":
                    q = self._quote(r["symbol"], "perp")
                    mmr = 0.5 / (q.max_leverage or 20) if q else 0.025
                    sgn = 1 if r["size"] > 0 else -1
                    p.liq_px = r["avg_px"] * (1 - sgn * (1 / lev - mmr))
                    p.liq_distance = abs(mark - p.liq_px) / mark
            out.append(p)
        return out

    async def account(self):
        c = self.db.q("SELECT * FROM paper_cash WHERE venue=?", (self.venue,))[0]
        eq = self._equity()
        return {"venue": self.venue, "mode": "paper", "equity": eq, "cash": c["cash"],
                "start": c["start"], "margin_used": self._margin_used(),
                "pnl": eq - c["start"], "resting_orders": len(self.resting)}

    # ---------------------------------------------------------------- fills
    def _fill_resting(self, oid: str, o: _Resting) -> None:
        size = o.size
        if o.reduce_only:
            size = min(size, abs(self._pos_row(o.symbol, o.kind)["size"]))
        self.resting.pop(oid, None)
        if size <= 0:
            self.db.update_order(oid, status="canceled", error="reduce-only: position already flat")
            return
        fee = self._apply_fill(o.symbol, o.kind, o.side, size, o.price, True, oid, o.pair_id)
        self.db.update_order(oid, status="filled", filled=size, avg_px=o.price, fee=fee)

    def on_trade(self, t) -> None:
        """A public trade print on this venue (StreamHub listener).

        A resting BUY at P is filled by taker SELLS: a sell printed below P went through us; a
        sell printed AT P first consumes the queue that was there before we joined. Mirror for
        sells. Unknown taker side counts for both (conservative only in queue terms)."""
        if t.venue != self.venue:
            return
        for oid, o in list(self.resting.items()):
            if o.symbol != t.symbol or o.kind != "perp" or t.ts < o.placed - 1:
                continue
            hits_us = (t.taker_buy is not True) if o.side == "buy" else (t.taker_buy is not False)
            if not hits_us:
                continue
            if (o.side == "buy" and t.px < o.price) or (o.side == "sell" and t.px > o.price):
                self._fill_resting(oid, o)
            elif abs(t.px - o.price) <= 1e-9 * o.price:
                o.queue_ahead -= t.sz
                if o.queue_ahead < 0:
                    self._fill_resting(oid, o)

    # ---------------------------------------------------------------- clock
    def tick(self, dt: float) -> None:
        # 1) resting post-only orders: fill when the book has moved through our price
        for oid, o in list(self.resting.items()):
            b = self._book(o.symbol, o.kind)
            if not b:
                continue
            through = (o.side == "buy" and b.ask < o.price) or (o.side == "sell" and b.bid > o.price)
            if through:
                self._fill_resting(oid, o)
        # 2) funding on perps: longs pay positive rates, continuously
        if dt <= 0:
            return
        for r in self.db.q("SELECT * FROM paper_positions WHERE venue=? AND kind='perp' AND size!=0",
                           (self.venue,)):
            q = self._quote(r["symbol"], "perp")
            if not q or q.funding_1h is None or not q.px:
                continue
            pay = -r["size"] * q.px * q.funding_1h * dt / 3600.0
            self._add_cash(pay)
            self.db.x("UPDATE paper_positions SET funding=funding+? WHERE venue=? AND symbol=? AND kind='perp'",
                      (pay, self.venue, r["symbol"]))
            self._funding_buf[r["symbol"]] = self._funding_buf.get(r["symbol"], 0.0) + pay
        if time.time() - self._last_flush >= 60 and self._funding_buf:
            for sym, amt in self._funding_buf.items():
                self.db.x("INSERT INTO funding_payments VALUES (?,?,?,?,?)",
                          (time.time(), self.venue, "paper", sym, amt))
            self._funding_buf.clear()
            self._last_flush = time.time()

    def reset(self) -> None:
        """Wipe this venue's paper ledger back to the starting balance."""
        self.resting.clear()
        start = float(self.cfg()["paper_balance_usd"])
        self.db.x("DELETE FROM paper_positions WHERE venue=?", (self.venue,))
        self.db.x("INSERT OR REPLACE INTO paper_cash VALUES (?,?,?)", (self.venue, start, start))
