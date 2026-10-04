"""Network outages: an order sent without a reply is recorded 'unknown', the leg stops, and once the
venue answers again the engine asks what became of it and cancels a resting one."""
import asyncio

import httpx
import pytest
import requests

from it45.store import Store
from it45.trading.brokers import REQUEST_TIMEOUT_S, Ack, no_reply
from it45.trading.db import TradeDB
from it45.trading.executor import Leg

from test_hl_testnet import FakeExchange, FakeInfo, make as make_hl
from test_testnet_connectors import RESTING, bulk
from test_trading import eng, run  # noqa: F401  (fixture)


# ------------------------------------------------------------------ classification
def test_no_reply_classification():
    req = httpx.Request("POST", "https://x")
    assert no_reply(httpx.ReadTimeout("t", request=req))           # sent, answer lost
    assert no_reply(httpx.RemoteProtocolError("dropped", request=req))
    assert no_reply(requests.exceptions.ReadTimeout())
    assert no_reply(requests.exceptions.ConnectionError())          # ambiguous: assume it may have arrived
    assert no_reply(TimeoutError())
    assert not no_reply(httpx.ConnectError("refused", request=req))  # never left the computer
    assert not no_reply(httpx.ConnectTimeout("t", request=req))
    assert not no_reply(requests.exceptions.ConnectTimeout())
    resp = httpx.Response(400, request=req)
    assert not no_reply(httpx.HTTPStatusError("bad", request=req, response=resp))   # venue answered
    assert not no_reply(ValueError("bad size"))
    assert REQUEST_TIMEOUT_S == 10.0


# ------------------------------------------------------------------ Bulk
class DropHttp:
    """Bulk HTTP fake whose /order call times out once, then answers normally."""

    def __init__(self, inner):
        self.inner, self.drop = inner, True

    def get(self, url, params=None):
        return self.inner.get(url, params)

    def post(self, url, json=None):
        if self.drop and "nonce" in json:      # the order itself, not the leverage setting
            self.drop = False
            raise httpx.ReadTimeout("timed out", request=httpx.Request("POST", url))
        return self.inner.post(url, json)


def test_bulk_no_reply_is_unknown_then_reconciled(tmp_path):
    b, sig, http = bulk(tmp_path, RESTING)
    b.http = DropHttp(http)
    a = asyncio.run(b.place(symbol="BTC", kind="perp", side="buy", size=0.001, price=84000, post_only=True))
    assert a.status == "unknown" and "no reply from Bulk" in a.error
    row = b.db.q("SELECT status, venue_order_id FROM orders WHERE id=?", (a.order_id,))[0]
    assert row["status"] == "unknown" and row["venue_order_id"] == "MY_OID"   # id known before sending
    # the order did reach Bulk and rests there: reconcile finds it and cancels it
    http.replies["openOrders"] = [{"openOrder": {"orderId": "MY_OID"}}]
    http.replies["cancel"] = {"status": "ok"}
    orig = http.post

    def post(url, json=None):
        last = sig.signed[-1] if sig.signed else None
        if isinstance(last, dict) and last.get("type") == "cancel":
            http.replies["openOrders"] = []
        return orig(url, json)
    http.post = post
    r = asyncio.run(b.reconcile(a.order_id))
    assert r.status == "canceled"
    assert any(s.get("type") == "cancel" and s.get("order_id") == "MY_OID" for s in sig.signed if isinstance(s, dict))


# ------------------------------------------------------------------ Hyperliquid
def test_hl_uses_cloid_and_reconciles_by_it(tmp_path):
    class Timeout(FakeExchange):
        def order(self, *a, **k):
            self.calls.append(("order", a, k))
            raise requests.exceptions.ReadTimeout("read timed out")

    class Info(FakeInfo):
        open_ = True

        def query_order_by_cloid(self, addr, cloid):
            self.asked = cloid.to_raw()
            return {"status": "order", "order": {"order": {"oid": 7, "coin": "BTC"}, "status": "open"}}

        def query_order_by_oid(self, addr, oid):
            return {"order": {"status": "open" if self.open_ else "canceled"}}

        def user_fills(self, addr):
            return []

    from it45.trading.hl_testnet import HyperliquidTestnet
    info, ex = Info(), Timeout(None)
    b = HyperliquidTestnet(TradeDB(Store(tmp_path / "t.db")), {"IT45_HL_ACCOUNT_ADDRESS": "0xabc"},
                           info=info, exchange=ex)
    a = asyncio.run(b.place(symbol="BTC", kind="perp", side="buy", size=0.5, price=100.0, post_only=True))
    assert a.status == "unknown"
    cloid = ex.calls[0][2]["cloid"].to_raw()
    assert cloid == HyperliquidTestnet._cloid(a.order_id).to_raw()

    def cancel(*args):
        ex.calls.append(("cancel", args, {}))
        info.open_ = False
        return {"status": "ok"}
    ex.cancel = cancel
    r = asyncio.run(b.reconcile(a.order_id))
    assert info.asked == cloid and r.status == "canceled"
    assert ex.calls[-1][0] == "cancel" and ex.calls[-1][1] == ("BTC", 7)


def test_hl_reconcile_order_never_arrived(tmp_path):
    class Info(FakeInfo):
        def query_order_by_cloid(self, addr, cloid):
            return {"status": "unknownOid"}
    from it45.trading.hl_testnet import HyperliquidTestnet
    b = HyperliquidTestnet(TradeDB(Store(tmp_path / "t.db")), {"IT45_HL_ACCOUNT_ADDRESS": "0xabc"},
                           info=Info(), exchange=FakeExchange(None))
    b.db.insert_order({"id": "H-x", "ts": 0, "updated_ts": 0, "venue": "hyperliquid", "mode": "testnet",
                       "symbol": "BTC", "kind": "perp", "side": "buy", "size": 1, "limit_px": 1, "post_only": 1,
                       "reduce_only": 0, "status": "unknown", "pair_id": None, "error": "no reply"})
    assert asyncio.run(b.reconcile("H-x")).status == "canceled"
    assert b.db.q("SELECT status FROM orders WHERE id='H-x'")[0]["status"] == "canceled"


# ------------------------------------------------------------------ executor + engine
class NoReplyBroker:
    """Wraps the paper broker: every order goes out and no answer comes back."""

    def __init__(self, inner):
        self.inner, self.sent, self.reconciled = inner, 0, []
        self.mode = inner.mode

    def __getattr__(self, k):
        return getattr(self.inner, k)

    async def place(self, **k):
        self.sent += 1
        oid = f"X-{self.sent}"
        self.inner.db.insert_order({"id": oid, "ts": 0, "updated_ts": 0, "venue": "bulk", "mode": "paper",
                                    "symbol": k["symbol"], "kind": k["kind"], "side": k["side"], "size": k["size"],
                                    "limit_px": k["price"], "post_only": int(k["post_only"]), "reduce_only": 0,
                                    "status": "unknown", "pair_id": None, "error": "no reply"})
        return Ack(oid, "unknown", error="no reply from Bulk (ReadTimeout)")

    async def reconcile(self, oid):
        self.reconciled.append(oid)
        self.inner.db.update_order(oid, status="canceled")
        return Ack(oid, "canceled")


def test_leg_stops_after_no_reply_and_engine_reconciles_when_back(eng):  # noqa: F811
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0, spread=0.0005)
    e.update_cfg({"exec": {"maker_attempts": 3, "taker_fallback": True}})
    nb = NoReplyBroker(e.brokers["bulk"])
    e.brokers["bulk"] = nb
    r = run(e.executor.run_leg(Leg("bulk", "BTC", "perp", "buy", 1.0), None, None, None))
    assert nb.sent == 1, "nothing more may be sent after an order with no reply"
    assert r.filled == 0 and "no reply" in r.error
    assert e.api_errors.get("bulk") == 1
    # venue still unreachable: no reconcile attempt
    feed.ages["bulk"] = 999
    run(e._reconcile_unknown())
    assert nb.reconciled == []
    # venue answers again: the order is looked up and the outcome logged
    feed.ages["bulk"] = 1
    run(e._reconcile_unknown())
    assert nb.reconciled == ["X-1"]
    assert e.db.q("SELECT status FROM orders WHERE id='X-1'")[0]["status"] == "canceled"
    assert e.db.q("SELECT COUNT(*) n FROM audit WHERE event='order_reconciled'")[0]["n"] == 1
