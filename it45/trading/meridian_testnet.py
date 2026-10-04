"""Meridian TESTNET broker: EIP-712 signed orders (eth_account), linked-signer friendly.

Keys come from .env, never from the dashboard:
    IT45_MERIDIAN_SIGNER_KEY   private key of a LINKED SIGNER (can trade, cannot withdraw)
    IT45_MERIDIAN_ACCOUNT      the owner wallet address the subaccount belongs to
    IT45_MERIDIAN_SUBACCOUNT   subaccount name (default "primary")
The signing domain and type strings are read live from /v1/rpc/config (never hard-coded), as
Meridian's docs require. Prices/quantities are signed as 9-decimal fixed point from the SAME
decimal strings that are sent in JSON.
Verified on 2026-10-03: a TradeOrder signed here passes testnet signature verification (the
API answers "sender not found" for an unfunded key, and "invalid signature" for a bad one).
Order lifecycle on a funded account is NOT yet verified live: the faucet needs a browser wallet.
"""
from __future__ import annotations

import asyncio
import random
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import httpx

from ..venues.base import norm_symbol
from .brokers import REQUEST_TIMEOUT_S, Ack, Book, Broker, Position, no_reply
from .db import new_id

TESTNET_URL = "https://api.meridiantest.net/v1"
SCALE = Decimal(10) ** 9


def _q(x: float, step: str, up: bool = False) -> Decimal:
    s = Decimal(step)
    return (Decimal(str(x)) / s).to_integral_value(ROUND_CEILING if up else ROUND_FLOOR) * s


def _types(spec: str) -> list[dict]:
    return [{"name": p.split()[1], "type": p.split()[0]} for p in spec.split(",")]


def subaccount_bytes32(name: str) -> str:
    return "0x" + name.encode().ljust(32, b"\0").hex()


class MeridianSigner:
    def __init__(self, key: str, config: dict):
        from eth_account import Account
        self.acct = Account.from_key(key if key.startswith("0x") else "0x" + key)
        self.domain = config["domain"]
        self.types = config["signatureTypes"]

    @property
    def address(self):
        return self.acct.address

    def sign(self, primary: str, message: dict) -> str:
        from eth_account.messages import encode_typed_data
        full = {"types": {"EIP712Domain": [{"name": "name", "type": "string"},
                                           {"name": "version", "type": "string"},
                                           {"name": "chainId", "type": "uint256"},
                                           {"name": "verifyingContract", "type": "address"}],
                          primary: _types(self.types[primary])},
                "primaryType": primary, "domain": self.domain, "message": message}
        sig = self.acct.sign_message(encode_typed_data(full_message=full)).signature.hex()
        return sig if sig.startswith("0x") else "0x" + sig


def nonce_ns() -> int:
    return int(time.time() * 1000) * 1_000_000 + random.randint(0, 999_999)


class MeridianTestnet(Broker):
    venue = "meridian"
    mode = "testnet"

    def __init__(self, db, env, client=None, signer=None):
        self.db = db
        self.http = client or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        key = (env.get("IT45_MERIDIAN_SIGNER_KEY") or "").strip()
        self.owner = (env.get("IT45_MERIDIAN_ACCOUNT") or "").strip()
        self.sub_name = (env.get("IT45_MERIDIAN_SUBACCOUNT") or "primary").strip()
        if signer is None:
            if not key or not self.owner:
                raise RuntimeError("Meridian testnet needs IT45_MERIDIAN_SIGNER_KEY and IT45_MERIDIAN_ACCOUNT "
                                   "in .env (see docs/03_keys.md)")
            signer = MeridianSigner(key, self._get("/rpc/config"))
        self.signer = signer
        self.sub = subaccount_bytes32(self.sub_name)
        prods = self._get("/product", limit=100).get("data", [])
        self.products = {norm_symbol(p.get("baseTokenName") or p["ticker"]): p
                         for p in prods if p.get("status") == "ACTIVE"}
        subs = self._get("/subaccount", sender=self.owner).get("data", [])
        match = [s for s in subs if s.get("name") in (self.sub, self.sub_name)]
        if not match:
            raise RuntimeError(f"no Meridian testnet subaccount '{self.sub_name}' for {self.owner}: "
                               "deposit from the faucet first (docs/03_keys.md)")
        self.sub_id = match[0]["id"]
        self.books: dict[str, Book] = {}

    # ---------------------------------------------------------------- http
    def _get(self, path, **params):
        r = self.http.get(TESTNET_URL + path, params=params or None)
        r.raise_for_status()
        return r.json()

    def _post(self, path, body):
        r = self.http.post(TESTNET_URL + path, json=body)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message")
            except Exception:
                msg = r.text[:200]
            raise RuntimeError(f"HTTP {r.status_code}: {msg}")
        return r.json()

    async def _run(self, fn, *a, **k):
        return await asyncio.to_thread(fn, *a, **k)

    def _prod(self, symbol, kind):
        if kind != "perp" or symbol not in self.products:
            raise KeyError(f"{symbol} {kind} is not listed on Meridian testnet")
        return self.products[symbol]

    # ---------------------------------------------------------------- books
    async def book(self, symbol, kind):
        try:
            p = self._prod(symbol, kind)
        except KeyError:
            return None
        d = await self._run(self._get, "/product/market-price", productIds=p["id"])
        row = (d.get("data") or [{}])[0]
        bid, ask = row.get("bestBidPrice"), row.get("bestAskPrice")
        if not bid or not ask:
            return None
        b = Book(float(bid), float(ask), 0.0, time.time())
        b.mid = (b.bid + b.ask) / 2
        self.books[symbol] = b
        return b

    def cached_mid(self, symbol, kind):
        b = self.books.get(symbol)
        return b.mid if b else None

    # ---------------------------------------------------------------- orders
    def build_order(self, p: dict, side: str, size: float, price: float, post_only: bool, reduce_only: bool):
        qty = _q(size, p["lotSize"])
        px = _q(price, p["tickSize"], up=(side == "sell"))
        nonce, signed_at = nonce_ns(), int(time.time())
        msg = {"sender": self.signer.address, "subaccount": self.sub,
               "quantity": int(qty * SCALE), "price": int(px * SCALE), "reduceOnly": reduce_only,
               "side": 0 if side == "buy" else 1, "engineType": 0, "productId": int(p["onchainId"]),
               "nonce": nonce, "signedAt": signed_at}
        data = {"sender": self.signer.address, "subaccount": self.sub, "quantity": format(qty.normalize(), "f"),
                "price": format(px.normalize(), "f"), "reduceOnly": reduce_only, "side": msg["side"],
                "engineType": 0, "onchainId": int(p["onchainId"]), "type": "LIMIT",
                "timeInForce": "GTD" if post_only else "IOC", "postOnly": bool(post_only),
                "nonce": str(nonce), "signedAt": signed_at}
        return {"data": data, "signature": self.signer.sign("TradeOrder", msg)}, float(qty), float(px)

    def _record(self, oid, symbol, side, size, price, post_only, reduce_only, pair_id, status, err=None, void=None):
        self.db.insert_order({"id": oid, "ts": time.time(), "updated_ts": time.time(), "venue": self.venue,
                              "mode": "testnet", "symbol": symbol, "kind": "perp", "side": side, "size": size,
                              "limit_px": price, "post_only": int(post_only), "reduce_only": int(reduce_only),
                              "status": status, "pair_id": pair_id, "error": err, "venue_order_id": void})

    async def place(self, *, symbol, kind, side, size, price, post_only, reduce_only=False, pair_id=None):
        oid = new_id("M")
        try:
            p = self._prod(symbol, kind)
            body, qty, px = self.build_order(p, side, size, price, post_only, reduce_only)
            if qty <= 0:
                raise ValueError(f"size {size} rounds to zero (lot {p['lotSize']})")
            res = await self._run(self._post, "/order", body)
        except Exception as e:
            msg = str(e)
            if no_reply(e):     # may have reached Meridian: reconcile() looks for it by nonce
                d = body["data"]
                err = f"no reply from Meridian ({type(e).__name__}); nonce {d['nonce']}"
                self._record(oid, symbol, side, qty, px, post_only, reduce_only, pair_id, "unknown", err)
                return Ack(oid, "unknown", error=err)
            if "post" in msg.lower() and ("cross" in msg.lower() or "match" in msg.lower()):
                msg = "post-only would cross the book"
            self._record(oid, symbol, side, size, price, post_only, reduce_only, pair_id, "rejected", msg)
            return Ack(oid, "rejected", error=msg)
        void = res.get("id")
        self._record(oid, symbol, side, qty, px, post_only, reduce_only, pair_id, "open", None, void)
        # taker orders settle in the next execution block: the real state comes from status()
        return Ack(oid, "open", float(res.get("filled") or 0))

    def _fills(self, void):
        d = self._get("/order/fill", subaccountId=self.sub_id, limit=100)
        mine = [f for f in d.get("data", []) if f.get("orderId") == void]
        sz = sum(float(f["filled"]) for f in mine)
        notional = sum(float(f["filled"]) * float(f["price"]) for f in mine)
        fee = sum(float(f.get("feeUsd") or 0) for f in mine)
        return sz, (notional / sz if sz else None), fee

    async def status(self, order_id):
        row = self.db.q("SELECT venue_order_id FROM orders WHERE id=?", (order_id,))
        void = row[0]["venue_order_id"] if row else None
        if not void:
            return Ack(order_id, "rejected", error="unknown order")
        o = await self._run(self._get, f"/order/{void}")
        s = (o.get("status") or "").upper()
        st = {"NEW": "open", "PENDING": "open", "FILLED_PARTIAL": "open", "FILLED": "filled"}.get(
            s, "rejected" if s == "REJECTED" else "canceled")
        sz, px, fee = await self._run(self._fills, void)
        if st == "canceled" and sz > 0:
            st = "filled"            # IOC remainder canceled after a partial fill
        self.db.update_order(order_id, status=st, filled=sz, avg_px=px, fee=fee,
                             error=o.get("rejectedReason") if st == "rejected" else None)
        return Ack(order_id, st, sz, px, fee, o.get("rejectedReason"))

    def _cancel_body(self, ids=None):
        nonce = nonce_ns()
        msg = {"sender": self.signer.address, "subaccount": self.sub, "nonce": nonce}
        body = {"data": {"sender": self.signer.address, "subaccount": self.sub, "nonce": str(nonce)},
                "signature": self.signer.sign("CancelOrder", msg)}
        if ids:
            body["data"]["orderIds"] = ids
        return body

    async def cancel(self, order_id):
        row = self.db.q("SELECT venue_order_id FROM orders WHERE id=?", (order_id,))
        if row and row[0]["venue_order_id"]:
            try:
                await self._run(self._post, "/order/cancel", self._cancel_body([row[0]["venue_order_id"]]))
            except Exception:
                pass
            await asyncio.sleep(1.0)    # cancels apply in the next execution block
        return await self.status(order_id)

    async def reconcile(self, order_id, give_up_s: float = 600.0):
        """An order sent without a reply: find it in the subaccount's orders by its signed nonce
        (or, failing that, by product, side, price and size placed after it), then cancel it if
        it rests. Not found after give_up_s: it never arrived."""
        row = self.db.q("SELECT * FROM orders WHERE id=?", (order_id,))
        if not row:
            return Ack(order_id, "rejected", error="unknown order")
        o = row[0]
        if not o["venue_order_id"]:
            import re
            m = re.search(r"nonce (\d+)", o["error"] or "")
            nonce = m.group(1) if m else None
            d = await self._run(self._get, "/order", subaccountId=self.sub_id, limit=100)
            pid = int(self._prod(o["symbol"], "perp")["onchainId"])
            side = 0 if o["side"] == "buy" else 1
            hit = None
            for x in d.get("data", []):
                if nonce and str(x.get("nonce")) == nonce:
                    hit = x
                    break
                same = (int(x.get("onchainId", x.get("productId", -1)) or -1) == pid and int(x.get("side", -1)) == side
                        and abs(float(x.get("price", 0)) - o["limit_px"]) < 1e-9
                        and abs(float(x.get("quantity", 0)) - o["size"]) < 1e-9)
                if same and hit is None:
                    hit = x
            if hit is None:
                if time.time() - o["ts"] > give_up_s:
                    self.db.update_order(order_id, status="canceled", error="no reply; Meridian has no such order")
                    return Ack(order_id, "canceled")
                return Ack(order_id, "unknown", error=o["error"])
            self.db.update_order(order_id, venue_order_id=hit.get("id"))
        a = await self.status(order_id)
        if a.status == "open":
            a = await self.cancel(order_id)
        return a

    async def cancel_all(self):
        r = await self._run(self._post, "/order/cancel-all", self._cancel_body())
        return len(r.get("data", []))

    # ---------------------------------------------------------------- views
    async def positions(self):
        d = await self._run(self._get, "/position", subaccountId=self.sub_id, open="true", limit=100)
        by_id = {p["id"]: s for s, p in self.products.items()}
        out = []
        for p in d.get("data", []):
            size = float(p.get("size") or 0)
            if size == 0:
                continue
            sym = by_id.get(p.get("productId"), p.get("productId"))
            signed = size if int(p.get("side", 0)) == 0 else -size
            cost = float(p.get("cost") or 0)
            entry = abs(cost / size) if size else 0
            mark = self.cached_mid(sym, "perp") or entry
            liq = float(p.get("liquidationPrice") or 0) or None
            out.append(Position(self.venue, sym, "perp", signed, entry, mark,
                                upnl=float(p.get("unrealizedPnl") or 0), realized=float(p.get("realizedPnl") or 0),
                                fees=float(p.get("feesAccruedUsd") or 0),
                                funding=-float(p.get("fundingAccruedUsd") or 0),
                                liq_px=liq, liq_distance=abs(mark - liq) / mark if liq and mark else None))
        return out

    async def account(self):
        d = await self._run(self._get, "/subaccount/balance", subaccountId=self.sub_id)
        amt = sum(float(b.get("amount") or 0) for b in d.get("data", []))
        avail = sum(float(b.get("available") or 0) for b in d.get("data", []))
        used = sum(float(b.get("totalUsed") or 0) for b in d.get("data", []))
        upnl = sum(p.upnl for p in await self.positions())
        return {"venue": self.venue, "mode": "testnet", "address": self.owner, "equity": amt + upnl,
                "cash": avail, "margin_used": used, "start": None}
