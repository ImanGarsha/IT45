"""Bulk TESTNET broker, signed with the official `bulk-keychain` library (Ed25519 over the
canonical wincode message, testnet signature domain).

Signing: it45/trading/bulk_sign.py (pure Python, byte-identical to the official bulk-keychain,
which has no Windows wheel for Python 3.13).

Keys come from .env, never from the dashboard:
    IT45_BULK_SIGNER_KEY   base58 secret key of the signing wallet (an agent wallet, or for testnet
                           a throwaway wallet that is itself the account)
    IT45_BULK_ACCOUNT      base58 public key of the account traded for (defaults to the signer)
Verified live on 2026-10-03 against https://exchange-api.bulk.trade/api/v1 with a throwaway
wallet: faucet, post-only rest, cancel, IOC open, reduce-only IOC close.
Only limit orders (ALO / IOC) and cancels are ever signed here; the Oct-2026 conditional-order
signing change does not touch them.
"""
from __future__ import annotations

import asyncio
import math
import time

import httpx

from ..venues.base import norm_symbol
from .brokers import Ack, Book, Broker, Position
from .db import new_id

TESTNET_URL = "https://exchange-api.bulk.trade/api/v1"


def _step_round(x: float, step: float) -> float:
    if not step:
        return x
    n = math.floor(x / step + 1e-9)
    return round(n * step, 10)


class BulkTestnet(Broker):
    venue = "bulk"
    mode = "testnet"

    def __init__(self, db, env, cfg_getter=None, client=None, signer=None, account=None):
        self.db = db
        self.cfg = cfg_getter
        if signer is None:
            key = (env.get("IT45_BULK_SIGNER_KEY") or "").strip()
            if not key:
                raise RuntimeError("Bulk testnet needs IT45_BULK_SIGNER_KEY in .env (see docs/03_keys.md)")
            from .bulk_sign import BulkSigner
            signer = BulkSigner(key, "testnet")
        self.signer = signer
        self.acct_pk = account or (env.get("IT45_BULK_ACCOUNT") or "").strip() or signer.pubkey
        self.http = client or httpx.Client(timeout=15)
        info = self._get("/exchangeInfo")
        self.markets = {norm_symbol(m["symbol"]): m for m in info}
        self.books: dict[str, Book] = {}
        self.syms: dict[str, str] = {}             # our id -> bulk symbol
        self.voids: dict[str, str] = {}            # our id -> bulk order id
        self._lev_set: set[str] = set()

    # ---------------------------------------------------------------- http
    def _get(self, path, **params):
        r = self.http.get(TESTNET_URL + path, params=params or None)
        r.raise_for_status()
        return r.json()

    def _post(self, path, body):
        r = self.http.post(TESTNET_URL + path, json=body)
        r.raise_for_status()
        return r.json()

    def _account_q(self, kind):
        return self._post("/account", {"type": kind, "user": self.acct_pk})

    def _send(self, tx: dict):
        return self._post("/order", {k: v for k, v in tx.items() if k != "order_id"})

    async def _run(self, fn, *a):
        return await asyncio.to_thread(fn, *a)

    def _mkt(self, symbol, kind):
        if kind != "perp":
            raise KeyError("Bulk has perps only")
        m = self.markets.get(symbol)
        if not m or m.get("status") != "TRADING":
            raise KeyError(f"{symbol} is not trading on Bulk testnet")
        return m

    # ---------------------------------------------------------------- books
    async def book(self, symbol, kind):
        try:
            m = self._mkt(symbol, kind)
        except KeyError:
            return None
        d = await self._run(lambda: self._get("/l2book", type="l2book", coin=m["symbol"], nlevels=1))
        bids, asks = d.get("levels", [[], []])
        if not bids or not asks:
            return None
        b = Book(float(bids[0]["px"]), float(asks[0]["px"]), 0.0, time.time())
        b.mid = (b.bid + b.ask) / 2
        self.books[symbol] = b
        return b

    def cached_mid(self, symbol, kind):
        b = self.books.get(symbol)
        return b.mid if b else None

    # ---------------------------------------------------------------- orders
    def _record(self, oid, symbol, side, size, price, post_only, reduce_only, pair_id, status, err=None, void=None):
        self.db.insert_order({"id": oid, "ts": time.time(), "updated_ts": time.time(), "venue": self.venue,
                              "mode": "testnet", "symbol": symbol, "kind": "perp", "side": side, "size": size,
                              "limit_px": price, "post_only": int(post_only), "reduce_only": int(reduce_only),
                              "status": status, "pair_id": pair_id, "error": err, "venue_order_id": void})

    def _ensure_leverage(self, bsym):
        if bsym in self._lev_set or self.cfg is None:
            return
        lev = float(self.cfg()["leverage"])
        try:
            self._send(self.signer.sign_user_settings([(bsym, lev)]))
            self._lev_set.add(bsym)
        except Exception:
            pass   # leverage is a cap on the venue side; the guard caps notional anyway

    async def place(self, *, symbol, kind, side, size, price, post_only, reduce_only=False, pair_id=None):
        oid = new_id("B")
        try:
            m = self._mkt(symbol, kind)
        except KeyError as e:
            self._record(oid, symbol, side, size, price, post_only, reduce_only, pair_id, "rejected", str(e))
            return Ack(oid, "rejected", error=str(e))
        sz = _step_round(size, float(m["lotSize"]))
        px = round(_step_round(price, float(m["tickSize"])) if side == "buy"
                   else math.ceil(price / float(m["tickSize"]) - 1e-9) * float(m["tickSize"]), 10)
        if sz <= 0 or sz * px < float(m.get("minNotional", 0)):
            msg = f"size {size} below Bulk minimum (lot {m['lotSize']}, min notional ${m.get('minNotional')})"
            self._record(oid, symbol, side, size, price, post_only, reduce_only, pair_id, "rejected", msg)
            return Ack(oid, "rejected", error=msg)
        await self._run(self._ensure_leverage, m["symbol"])
        order = {"type": "order", "symbol": m["symbol"], "is_buy": side == "buy", "price": px, "size": sz,
                 "reduce_only": reduce_only, "order_type": {"type": "limit", "tif": "ALO" if post_only else "IOC"}}
        try:
            tx = self.signer.sign(order)
            res = await self._run(self._send, tx)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            self._record(oid, symbol, side, sz, px, post_only, reduce_only, pair_id, "rejected", msg)
            return Ack(oid, "rejected", error=msg)
        void = tx.get("order_id")
        ack = self._parse(oid, res, void)
        self.voids[oid], self.syms[oid] = void, m["symbol"]
        self._record(oid, symbol, side, sz, px, post_only, reduce_only, pair_id, ack.status, ack.error, void)
        if ack.filled:
            self.db.update_order(oid, filled=ack.filled, avg_px=ack.avg_px)
        return ack

    @staticmethod
    def _parse(oid, res, void) -> Ack:
        if not isinstance(res, dict) or res.get("status") != "ok":
            return Ack(oid, "rejected", error=str(res)[:300])
        sts = (res.get("response") or {}).get("data", {}).get("statuses", [])
        mine = [s for s in sts if (next(iter(s.values())) or {}).get("oid") == void] or sts[-1:]
        if not mine:
            return Ack(oid, "rejected", error="empty response")
        (k, v), = mine[0].items()
        if k == "resting":
            return Ack(oid, "open")
        if k == "filled":
            return Ack(oid, "filled", float(v["totalSz"]), float(v["avgPx"]))
        if k == "working":   # partially filled, remainder resting
            return Ack(oid, "open", float(v.get("filledSz", 0)), float(v.get("vwap") or 0) or None)
        if k in ("cancelled", "canceled"):
            return Ack(oid, "canceled", error="IOC: nothing filled")
        msg = v.get("message") if isinstance(v, dict) else str(v)
        if msg and ("post" in msg.lower() and ("cross" in msg.lower() or "match" in msg.lower())):
            return Ack(oid, "rejected", error="post-only would cross the book")
        return Ack(oid, "rejected", error=f"{k}: {msg}")

    def _fills_for(self, void):
        d = self._account_q("fills")
        rows = d.get("data", d) if isinstance(d, dict) else d
        mine = [f for f in rows if void in (f.get("orderIdTaker"), f.get("orderIdMaker"))
                and self.acct_pk in (f.get("taker"), f.get("maker"))]
        sz = sum(float(f["amount"]) for f in mine)
        notional = sum(float(f["amount"]) * float(f["price"]) for f in mine)
        fee = sum(float(f.get("takerFee" if f.get("taker") == self.acct_pk else "makerFee") or 0) for f in mine)
        return sz, (notional / sz if sz else None), fee

    async def status(self, order_id):
        void = self.voids.get(order_id)
        if not void:
            return Ack(order_id, "rejected", error="unknown order")
        oo = await self._run(self._account_q, "openOrders")
        live = [o["openOrder"] for o in oo if o.get("openOrder", {}).get("orderId") == void]
        sz, px, fee = await self._run(self._fills_for, void)
        if live:
            st = "open"
        else:
            row = self.db.q("SELECT size FROM orders WHERE id=?", (order_id,))
            full = row[0]["size"] if row else sz
            st = "filled" if sz >= full * (1 - 1e-6) and sz > 0 else ("canceled" if sz == 0 else "filled")
        self.db.update_order(order_id, status=st, filled=sz, avg_px=px, fee=fee)
        return Ack(order_id, st, sz, px, fee)

    async def cancel(self, order_id):
        void, sym = self.voids.get(order_id), self.syms.get(order_id)
        if void and sym:
            try:
                await self._run(self._send, self.signer.sign({"type": "cancel", "symbol": sym, "order_id": void}))
            except Exception:
                pass
        return await self.status(order_id)

    async def cancel_all(self):
        oo = await self._run(self._account_q, "openOrders")
        if oo:
            await self._run(self._send, self.signer.sign({"type": "cancel_all"}))
        return len(oo)

    # ---------------------------------------------------------------- views
    def _full(self):
        d = self._account_q("fullAccount")
        return d[0]["fullAccount"] if d else {}

    async def positions(self):
        fa = await self._run(self._full)
        out = []
        for p in fa.get("positions", []):
            size = float(p.get("size") or 0)
            if size == 0:
                continue
            mark = float(p.get("fairPrice") or p.get("price") or 0)
            liq = float(p.get("liquidationPrice") or 0) or None
            out.append(Position(self.venue, norm_symbol(p["symbol"]), "perp", size, float(p.get("price") or 0),
                                mark, upnl=float(p.get("unrealizedPnl") or 0),
                                realized=float(p.get("realizedPnl") or 0), fees=float(p.get("fees") or 0),
                                funding=float(p.get("funding") or 0), liq_px=liq,
                                liq_distance=abs(mark - liq) / mark if liq and mark else None))
        return out

    async def account(self):
        fa = await self._run(self._full)
        m = fa.get("margin", {})
        eq = float(m.get("totalMargin") or 0) + float(m.get("unrealizedPnl") or 0)
        return {"venue": self.venue, "mode": "testnet", "address": self.acct_pk, "equity": eq,
                "cash": float(m.get("availableMargin") or 0), "margin_used": float(m.get("marginUsed") or 0),
                "start": None}
