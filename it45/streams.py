"""Live websocket streams: top of book + public trades per venue, for the symbols IT45 cares about.

Why: the REST feed refreshes every 5 s, so in paper mode a resting post-only order almost never
filled (the market had to move THROUGH it between two polls). With trade prints we can model a
queue: a resting bid at P fills once taker SELL volume printed at P exceeds the size that was
already resting there when we joined (or immediately if a sell prints below P).

Formats verified live 2026-10-03 (see docs/04_streams.md). Each venue stream:
- subscribes only to symbols the engine asks for (open legs, resting orders, top candidates)
- keeps `tops[symbol] = Top(bid, bid_sz, ask, ask_sz, ts)` and calls `on_trade(...)` listeners
- reconnects with backoff and re-subscribes; a stale top (> FRESH_S) is ignored by consumers
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import httpx


def quiet_websocket_handshake_noise(loop: asyncio.AbstractEventLoop, store=None) -> None:
    """Hide a harmless traceback from the websockets library.

    When a secure websocket connection fails before it is fully open (TLS handshake error or
    timeout, seen on Windows with Python 3.13), websockets' ClientConnection.connection_lost()
    touches `recv_messages`, which only exists after connection_made(). asyncio then prints
    "AttributeError: 'ClientConnection' object has no attribute 'recv_messages'" for every
    failed attempt. The attempt has already failed and the stream loop retries with backoff, so
    nothing is lost; the real reason shows under Venue health -> Live book. This filter drops that
    one traceback (logging one line to the event log at most every 10 minutes) and passes every
    other loop error through unchanged."""
    last = [0.0]

    def handler(lp, ctx):
        exc = ctx.get("exception")
        if isinstance(exc, AttributeError) and "recv_messages" in str(exc):
            if store is not None and time.time() - last[0] > 600:
                last[0] = time.time()
                store.log("warn", "streams", "a live-book websocket failed while opening; it retries "
                                             "automatically (see Venue health, Live book, for the reason)")
            return
        lp.default_exception_handler(ctx)

    loop.set_exception_handler(handler)

FRESH_S = 5.0


@dataclass
class Top:
    bid: float
    bid_sz: float
    ask: float
    ask_sz: float
    ts: float


@dataclass
class Trade:
    venue: str
    symbol: str
    px: float
    sz: float
    taker_buy: bool | None
    ts: float


class VenueStream:
    venue = ""
    url = ""

    def __init__(self, hub):
        self.hub = hub
        self.wanted: set[str] = set()       # normalised symbols
        self.subscribed: set[str] = set()
        self.tops: dict[str, Top] = {}
        self.ws = None
        self.connected = False
        self.error: str | None = None
        self.msgs = 0
        self.last_msg = 0.0
        self.task: asyncio.Task | None = None
        self.books: dict[str, tuple[dict, dict]] = {}   # delta-maintained books (Reya)

    # -- venue specifics -----------------------------------------------------
    def raw(self, symbol: str) -> str | None:
        return self.hub.raw_symbol(self.venue, symbol)

    def sub_msgs(self, symbol: str, raw: str) -> list[dict]:
        raise NotImplementedError

    def unsub_msgs(self, symbol: str, raw: str) -> list[dict]:
        return []

    def handle(self, m: dict) -> None:
        raise NotImplementedError

    # -- plumbing ------------------------------------------------------------
    def sym_of(self, raw: str) -> str | None:
        return self.hub.norm_of(self.venue, raw)

    def set_top(self, symbol, bid, bid_sz, ask, ask_sz):
        if symbol and bid and ask and bid < ask:
            self.tops[symbol] = Top(float(bid), float(bid_sz or 0), float(ask), float(ask_sz or 0), time.time())

    def trade(self, symbol, px, sz, taker_buy, ts=None):
        if symbol:
            self.hub.emit(Trade(self.venue, symbol, float(px), float(sz), taker_buy, ts or time.time()))

    async def restart(self):
        """Drop the current websocket (or its retry wait) and connect again now."""
        t, self.task = self.task, None
        if t is not None:
            t.cancel()
            try:
                await t
            except BaseException:
                pass
        self.ws, self.connected, self.error = None, False, None
        self.subscribed.clear()
        self.books.clear()
        if self.wanted:
            self.task = asyncio.create_task(self._run())

    async def want(self, symbols: set[str]):
        self.wanted = set(symbols)
        if self.task is None and self.wanted:
            self.task = asyncio.create_task(self._run())
        if self.connected:
            await self._sync()

    async def _sync(self):
        for s in sorted(self.wanted - self.subscribed):
            raw = self.raw(s)
            if not raw:
                continue
            for m in self.sub_msgs(s, raw):
                await self.ws.send(json.dumps(m))
            self.subscribed.add(s)
        for s in sorted(self.subscribed - self.wanted):
            raw = self.raw(s)
            for m in self.unsub_msgs(s, raw) if raw else []:
                await self.ws.send(json.dumps(m))
            self.subscribed.discard(s)
            self.tops.pop(s, None)
            self.books.pop(s, None)

    async def _run(self):
        import websockets
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.url, open_timeout=15, ping_interval=20,
                                              max_size=8 * 2**20) as ws:
                    self.ws, self.connected, self.error = ws, True, None
                    self.subscribed.clear()
                    self.books.clear()
                    await self._sync()
                    backoff = 1.0
                    async for raw in ws:
                        self.msgs += 1
                        self.last_msg = time.time()
                        try:
                            m = json.loads(raw)
                        except ValueError:
                            continue
                        try:
                            self.handle(m)
                        except Exception as e:   # one bad frame must not drop the stream
                            self.error = f"parse: {e!r}"[:200]
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:200]
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    def status(self) -> dict:
        fresh = [s for s, t in self.tops.items() if time.time() - t.ts < FRESH_S]
        return {"venue": self.venue, "connected": self.connected, "wanted": sorted(self.wanted),
                "fresh": sorted(fresh), "messages": self.msgs,
                "last_msg_age_s": (time.time() - self.last_msg) if self.last_msg else None,
                "error": self.error}


# ---------------------------------------------------------------- venues
class HyperliquidStream(VenueStream):
    venue, url = "hyperliquid", "wss://api.hyperliquid.xyz/ws"

    def sub_msgs(self, s, raw):
        return [{"method": "subscribe", "subscription": {"type": t, "coin": raw}} for t in ("bbo", "trades")]

    def unsub_msgs(self, s, raw):
        return [{"method": "unsubscribe", "subscription": {"type": t, "coin": raw}} for t in ("bbo", "trades")]

    def handle(self, m):
        ch, d = m.get("channel"), m.get("data")
        if ch == "bbo":
            b, a = d["bbo"]
            if b and a:
                self.set_top(self.sym_of(d["coin"]), b["px"], b["sz"], a["px"], a["sz"])
        elif ch == "trades":
            for t in d:
                self.trade(self.sym_of(t["coin"]), t["px"], t["sz"], t["side"] == "B", t["time"] / 1e3)


class ArcusStream(VenueStream):
    venue, url = "arcus", "wss://api.arcus.xyz/v1/ws"

    def sub_msgs(self, s, raw):
        return [{"type": "subscribe", "channel": c, "id": raw} for c in ("bbo", "trades")]

    def unsub_msgs(self, s, raw):
        return [{"type": "unsubscribe", "channel": c, "id": raw} for c in ("bbo", "trades")]

    def handle(self, m):
        ch, c = m.get("channel"), m.get("contents")
        if m.get("type") not in ("subscribed", "channel_data") or not c:
            return
        sym = self.sym_of(m.get("id", ""))
        if ch == "bbo":
            b, a = c.get("bestBid") or {}, c.get("bestAsk") or {}
            self.set_top(sym, b.get("price"), b.get("size"), a.get("price"), a.get("size"))
        elif ch == "trades" and isinstance(c, list):
            for t in c:
                self.trade(sym, t["price"], t["size"], t.get("side") == "BUY", t.get("timestamp", 0) / 1e6)


class BulkStream(VenueStream):
    venue, url = "bulk", "wss://mainnet-ws1.bulk.trade"

    def sub_msgs(self, s, raw):
        return [{"method": "subscribe", "subscription": [{"type": "l2Snapshot", "symbol": raw, "nlevels": 1},
                                                         {"type": "trades", "symbol": raw}]}]

    def unsub_msgs(self, s, raw):
        return [{"method": "unsubscribe", "topic": f"l2snapshot.{raw}.1"},
                {"method": "unsubscribe", "topic": f"trades.{raw}"}]

    def handle(self, m):
        t, d = m.get("type"), m.get("data") or {}
        if t == "l2Snapshot":
            book = d.get("book", {})
            bids, asks = (book.get("levels") or [[], []])[:2]
            if bids and asks:
                self.set_top(self.sym_of(book.get("symbol", "")), bids[0]["px"], bids[0]["sz"],
                             asks[0]["px"], asks[0]["sz"])
        elif t == "trades":
            for x in d.get("trades", []):
                self.trade(self.sym_of(x["s"]), x["px"], x["sz"], bool(x.get("side")), x.get("time", 0) / 1e3)


class ReyaStream(VenueStream):
    venue, url = "reya", "wss://ws.reya.xyz/"

    def sub_msgs(self, s, raw):
        return [{"type": "subscribe", "channel": f"/v2/market/{raw}/depth"},
                {"type": "subscribe", "channel": f"/v2/market/{raw}/perpExecutions"}]

    def unsub_msgs(self, s, raw):
        return [{"type": "unsubscribe", "channel": f"/v2/market/{raw}/depth"},
                {"type": "unsubscribe", "channel": f"/v2/market/{raw}/perpExecutions"}]

    def handle(self, m):
        ch = m.get("channel") or ""
        parts = ch.split("/")
        if len(parts) < 5:
            return
        sym = self.sym_of(parts[3])
        kind = parts[4]
        if kind == "depth":
            body = m.get("contents") if m.get("type") == "subscribed" else m.get("data")
            if not body:
                return
            if body.get("type") == "SNAPSHOT" or sym not in self.books:
                self.books[sym] = ({}, {})
            bids, asks = self.books[sym]
            for side, store in ((body.get("bids", []), bids), (body.get("asks", []), asks)):
                for lv in side:
                    px, q = float(lv["px"]), float(lv["qty"])
                    if q == 0:
                        store.pop(px, None)
                    else:
                        store[px] = q
            if bids and asks:
                bb, ba = max(bids), min(asks)
                self.set_top(sym, bb, bids[bb], ba, asks[ba])
        elif kind == "perpExecutions" and m.get("type") == "channel_data":
            for t in m.get("data") or []:
                self.trade(sym, t["price"], t["qty"], t.get("side") == "B", t.get("timestamp", 0) / 1e3)


class MeridianStream(VenueStream):
    venue, url = "meridian", "wss://ws.meridian.xyz/v1/stream"

    def sub_msgs(self, s, raw):
        return [{"event": "subscribe", "data": {"type": t, "symbol": raw}} for t in ("Ticker", "TradeFill")]

    def unsub_msgs(self, s, raw):
        return [{"event": "unsubscribe", "data": {"type": t, "symbol": raw}} for t in ("Ticker", "TradeFill")]

    def handle(self, m):
        e, d = m.get("e"), m.get("data") or {}
        sym = self.sym_of(d.get("s", ""))
        if e == "Ticker":
            self.set_top(sym, d.get("bidPx"), d.get("bidAmt"), d.get("askPx"), d.get("askAmt"))
        elif e == "TradeFill":
            for t in d.get("d", []):
                self.trade(sym, t["px"], t["sz"], int(t.get("sd", 0)) == 0, d.get("t", 0) / 1e3)


STREAMS = {c.venue: c for c in (HyperliquidStream, ArcusStream, BulkStream, ReyaStream, MeridianStream)}


class StreamHub:
    """Owns one stream per venue and the symbol maps between IT45 and each venue's stream ids."""

    def __init__(self, feed):
        self.feed = feed
        self.streams = {v: cls(self) for v, cls in STREAMS.items()}
        self.listeners: list = []
        self._meridian_tickers: dict[str, str] | None = None

    def raw_symbol(self, venue: str, symbol: str) -> str | None:
        if venue == "meridian":
            return (self._meridian_tickers or {}).get(symbol)
        for q in self.feed.quotes.get(venue, []):
            if q.symbol == symbol and q.kind == "perp":
                return q.raw_symbol
        return None

    def norm_of(self, venue: str, raw: str) -> str | None:
        if venue == "meridian":
            inv = {v: k for k, v in (self._meridian_tickers or {}).items()}
            return inv.get(raw)
        for q in self.feed.quotes.get(venue, []):
            if q.raw_symbol == raw and q.kind == "perp":
                return q.symbol
        return None

    async def _load_meridian(self):
        if self._meridian_tickers is not None:
            return
        from .venues.base import norm_symbol
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get("https://api.meridian.xyz/v1/product", params={"limit": 100})
                self._meridian_tickers = {norm_symbol(p.get("baseTokenName") or p["ticker"]): p["ticker"]
                                          for p in r.json().get("data", [])}
        except Exception:
            self._meridian_tickers = None

    async def want(self, by_venue: dict[str, set[str]]):
        if by_venue.get("meridian"):
            await self._load_meridian()
        for v, s in self.streams.items():
            await s.want(by_venue.get(v, set()))

    def top(self, venue: str, symbol: str) -> Top | None:
        s = self.streams.get(venue)
        t = s.tops.get(symbol) if s else None
        return t if t and time.time() - t.ts < FRESH_S else None

    def emit(self, trade: Trade):
        for fn in self.listeners:
            try:
                fn(trade)
            except Exception:
                pass

    def status(self) -> list[dict]:
        return [s.status() for s in self.streams.values()]

    async def reconnect(self):
        """Reconnect every live-book stream now, skipping any backoff wait."""
        for s in self.streams.values():
            await s.restart()

    async def stop(self):
        for s in self.streams.values():
            if s.task:
                s.task.cancel()
