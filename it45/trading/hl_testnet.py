"""Hyperliquid TESTNET broker (official hyperliquid-python-sdk), signed by an AGENT key.

Keys come from .env, never from the dashboard:
    IT45_HL_AGENT_KEY        private key of an agent (API) wallet approved by your account
    IT45_HL_ACCOUNT_ADDRESS  the account (main wallet) address the agent trades for
An agent wallet can place and cancel orders but cannot withdraw or transfer funds; this class
additionally only ever calls Exchange.order / cancel / update_leverage (asserted in tests).

Prices for limit orders come from the TESTNET book (testnet prices differ from mainnet).
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time

from ..venues.base import norm_symbol
from .brokers import REQUEST_TIMEOUT_S, Ack, Book, Broker, Position, no_reply
from .db import new_id

TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
ALLOWED_EXCHANGE_CALLS = {"order", "cancel", "update_leverage"}


def round_px(px: float, sz_decimals: int, spot: bool) -> float:
    """Hyperliquid tick rules: <= 5 significant figures and <= (6|8 - szDecimals) decimals."""
    if px <= 0:
        return px
    max_dec = (8 if spot else 6) - sz_decimals
    sig = float(f"{px:.5g}")
    return round(sig, max(max_dec, 0))


def round_sz(sz: float, sz_decimals: int) -> float:
    step = 10 ** (-sz_decimals)
    return math.floor(sz / step + 1e-9) * step


class _Guarded:
    """Wraps the SDK Exchange so nothing outside ALLOWED_EXCHANGE_CALLS can be reached."""

    def __init__(self, ex):
        self._ex = ex

    def __getattr__(self, name):
        if name not in ALLOWED_EXCHANGE_CALLS:
            raise PermissionError(f"IT45 does not call Exchange.{name}")
        return getattr(self._ex, name)


class HyperliquidTestnet(Broker):
    venue = "hyperliquid"
    mode = "testnet"

    def __init__(self, db, env, info=None, exchange=None):
        self.db = db
        key = (env.get("IT45_HL_AGENT_KEY") or "").strip()
        self.address = (env.get("IT45_HL_ACCOUNT_ADDRESS") or "").strip()
        if info is None or exchange is None:
            if not key or not self.address:
                raise RuntimeError("testnet needs IT45_HL_AGENT_KEY and IT45_HL_ACCOUNT_ADDRESS in .env "
                                   "(see docs/03_keys.md)")
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            wallet = Account.from_key(key if key.startswith("0x") else "0x" + key)
            info = Info(TESTNET_URL, skip_ws=True, timeout=REQUEST_TIMEOUT_S)
            exchange = Exchange(wallet, TESTNET_URL, account_address=self.address, timeout=REQUEST_TIMEOUT_S)
        self.info = info
        self.ex = _Guarded(exchange)
        self._load_meta()
        self.books: dict[tuple, Book] = {}
        self.oids: dict[str, int] = {}          # our id -> venue oid
        self.coins: dict[str, str] = {}         # our id -> coin name

    def _load_meta(self):
        meta = self.info.meta()
        self.perp = {norm_symbol(u["name"]): (u["name"], int(u["szDecimals"])) for u in meta["universe"]}
        sm = self.info.spot_meta()
        tok = {t["index"]: t for t in sm["tokens"]}
        self.spot = {}
        for u in sm["universe"]:
            b, q = tok.get(u["tokens"][0]), tok.get(u["tokens"][1])
            if b and q and q["name"] == "USDC":
                self.spot.setdefault(norm_symbol(b["name"]), (u["name"], int(b["szDecimals"])))

    def _coin(self, symbol, kind):
        m = (self.spot if kind == "spot" else self.perp).get(symbol)
        if not m:
            raise KeyError(f"{symbol} {kind} is not listed on Hyperliquid testnet")
        return m

    async def _call(self, fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    # ---------------------------------------------------------------- books
    async def book(self, symbol, kind):
        try:
            coin, _ = self._coin(symbol, kind)
            snap = await self._call(self.info.l2_snapshot, coin)
            bids, asks = snap["levels"]
            if not bids or not asks:
                return None
            b = Book(float(bids[0]["px"]), float(asks[0]["px"]), 0.0, time.time())
            b.mid = (b.bid + b.ask) / 2
            self.books[(symbol, kind)] = b
            return b
        except KeyError:
            return None

    def cached_mid(self, symbol, kind):
        b = self.books.get((symbol, kind))
        return b.mid if b else None

    # ---------------------------------------------------------------- orders
    def _record(self, oid, symbol, kind, side, size, price, post_only, reduce_only, pair_id, status, err=None,
                venue_oid=None):
        self.db.insert_order({"id": oid, "ts": time.time(), "updated_ts": time.time(), "venue": self.venue,
                              "mode": "testnet", "symbol": symbol, "kind": kind, "side": side, "size": size,
                              "limit_px": price, "post_only": int(post_only), "reduce_only": int(reduce_only),
                              "status": status, "pair_id": pair_id, "error": err,
                              "venue_order_id": str(venue_oid) if venue_oid else None})

    async def place(self, *, symbol, kind, side, size, price, post_only, reduce_only=False, pair_id=None):
        oid = new_id("H")
        try:
            coin, szd = self._coin(symbol, kind)
        except KeyError as e:
            self._record(oid, symbol, kind, side, size, price, post_only, reduce_only, pair_id, "rejected", str(e))
            return Ack(oid, "rejected", error=str(e))
        sz = round_sz(size, szd)
        px = round_px(price, szd, kind == "spot")
        if sz <= 0:
            msg = f"size {size} rounds to zero ({szd} decimals)"
            self._record(oid, symbol, kind, side, size, price, post_only, reduce_only, pair_id, "rejected", msg)
            return Ack(oid, "rejected", error=msg)
        tif = "Alo" if post_only else "Ioc"
        try:
            res = await self._call(self.ex.order, coin, side == "buy", sz, px, {"limit": {"tif": tif}},
                                   reduce_only=reduce_only, cloid=self._cloid(oid))
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if no_reply(e):     # may have reached Hyperliquid: reconcile() finds it by client order id
                self.coins[oid] = coin
                self._record(oid, symbol, kind, side, sz, px, post_only, reduce_only, pair_id, "unknown",
                             f"no reply from Hyperliquid ({msg})")
                return Ack(oid, "unknown", error=f"no reply from Hyperliquid ({msg})")
            self._record(oid, symbol, kind, side, sz, px, post_only, reduce_only, pair_id, "rejected", msg)
            return Ack(oid, "rejected", error=msg)
        ack = self._parse(oid, res)
        self.coins[oid] = coin
        if ack.status == "open":
            pass
        status = ack.status
        self._record(oid, symbol, kind, side, sz, px, post_only, reduce_only, pair_id, status, ack.error,
                     self.oids.get(oid))
        if ack.status == "filled":
            self.db.update_order(oid, filled=ack.filled, avg_px=ack.avg_px)
        return ack

    def _parse(self, oid, res) -> Ack:
        if not isinstance(res, dict) or res.get("status") != "ok":
            return Ack(oid, "rejected", error=str(res))
        try:
            st = res["response"]["data"]["statuses"][0]
        except Exception:
            return Ack(oid, "rejected", error=str(res))
        if "resting" in st:
            self.oids[oid] = int(st["resting"]["oid"])
            return Ack(oid, "open")
        if "filled" in st:
            f = st["filled"]
            self.oids[oid] = int(f["oid"])
            return Ack(oid, "filled", float(f["totalSz"]), float(f["avgPx"]))
        if "error" in st:
            err = st["error"]
            # post-only that would cross comes back as an error string from HL
            if "would have immediately matched" in err.lower() or "post only" in err.lower():
                return Ack(oid, "rejected", error="post-only would cross the book")
            return Ack(oid, "rejected", error=err)
        return Ack(oid, "rejected", error=str(st))

    async def _fills(self, voids: int):
        fills = await self._call(self.info.user_fills, self.address)
        mine = [f for f in fills if int(f.get("oid", -1)) == voids]
        sz = sum(float(f["sz"]) for f in mine)
        notional = sum(float(f["sz"]) * float(f["px"]) for f in mine)
        fee = sum(float(f.get("fee", 0)) for f in mine)
        return sz, (notional / sz if sz else None), fee

    async def status(self, order_id):
        v = self.oids.get(order_id)
        if v is None:
            return Ack(order_id, "rejected", error="unknown order")
        st = await self._call(self.info.query_order_by_oid, self.address, v)
        s = (st.get("order") or {}).get("status", "unknown")
        sz, px, fee = await self._fills(v)
        status = {"open": "open", "filled": "filled"}.get(s, "canceled" if "cancel" in s or "rejected" in s else s)
        self.db.update_order(order_id, status=status, filled=sz, avg_px=px, fee=fee)
        return Ack(order_id, status, sz, px, fee)

    async def cancel(self, order_id):
        v, coin = self.oids.get(order_id), self.coins.get(order_id)
        if v is not None and coin:
            try:
                await self._call(self.ex.cancel, coin, v)
            except Exception:
                pass
        return await self.status(order_id)

    @staticmethod
    def _cloid(oid):
        """Client order id derived from our id, so an order sent without a reply can be found."""
        from hyperliquid.utils.types import Cloid
        return Cloid("0x" + hashlib.md5(oid.encode()).hexdigest())

    async def reconcile(self, order_id):
        """An order sent without a reply: ask Hyperliquid by client order id; cancel it if it rests."""
        if order_id not in self.oids:
            r = await self._call(self.info.query_order_by_cloid, self.address, self._cloid(order_id))
            o = (r or {}).get("order") or {}
            if (r or {}).get("status") != "order" or not o:
                self.db.update_order(order_id, status="canceled", error="no reply; Hyperliquid has no such order")
                return Ack(order_id, "canceled")
            self.oids[order_id] = int(o["order"]["oid"])
            self.coins.setdefault(order_id, o["order"].get("coin"))
        a = await self.status(order_id)
        if a.status == "open":
            a = await self.cancel(order_id)
        return a

    async def cancel_all(self):
        n = 0
        for o in await self._call(self.info.open_orders, self.address):
            try:
                await self._call(self.ex.cancel, o["coin"], int(o["oid"]))
                n += 1
            except Exception:
                pass
        return n

    # ---------------------------------------------------------------- views
    async def positions(self):
        st = await self._call(self.info.user_state, self.address)
        name_to_sym = {v[0]: k for k, v in self.perp.items()}
        out = []
        for ap in st.get("assetPositions", []):
            p = ap.get("position", {})
            szi = float(p.get("szi") or 0)
            if szi == 0:
                continue
            sym = name_to_sym.get(p.get("coin"), p.get("coin"))
            entry = float(p.get("entryPx") or 0)
            mark = abs(float(p.get("positionValue") or 0) / szi) or entry
            liq = float(p["liquidationPx"]) if p.get("liquidationPx") else None
            out.append(Position(self.venue, sym, "perp", szi, entry, mark,
                                upnl=float(p.get("unrealizedPnl") or 0),
                                funding=-float((p.get("cumFunding") or {}).get("sinceOpen") or 0),
                                liq_px=liq, liq_distance=abs(mark - liq) / mark if liq and mark else None))
        sp = await self._call(self.info.spot_user_state, self.address)
        spot_by_token = {norm_symbol(k): k for k in self.spot}
        for b in sp.get("balances", []):
            tok = norm_symbol(b.get("coin", ""))
            total = float(b.get("total") or 0)
            if tok == "USDC" or total == 0 or tok not in self.spot:
                continue
            mid = self.cached_mid(tok, "spot")
            entry = float(b.get("entryNtl") or 0) / total if total else 0
            out.append(Position(self.venue, spot_by_token.get(tok, tok), "spot", total, entry, mid or entry))
        return out

    async def account(self):
        st = await self._call(self.info.user_state, self.address)
        ms = st.get("marginSummary", {})
        return {"venue": self.venue, "mode": "testnet", "address": self.address,
                "equity": float(ms.get("accountValue") or 0), "cash": float(st.get("withdrawable") or 0),
                "margin_used": float(ms.get("totalMarginUsed") or 0), "start": None}
