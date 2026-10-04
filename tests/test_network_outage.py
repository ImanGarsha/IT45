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


# ------------------------------------------------------------------ outage must not close anything
class DownBroker:
    """A venue connector while the network is down: every call fails."""

    def __init__(self, inner):
        self.inner, self.down = inner, True
        self.mode, self.venue = inner.mode, inner.venue

    def __getattr__(self, k):
        return getattr(self.inner, k)

    async def positions(self):
        if self.down:
            raise httpx.ConnectError("offline")
        return await self.inner.positions()

    async def account(self):
        if self.down:
            raise httpx.ConnectError("offline")
        return await self.inner.account()

    async def place(self, **k):
        if self.down:
            raise AssertionError("no order may be sent to an unreachable venue")
        return await self.inner.place(**k)


def _open_pair(e, feed):
    from it45.trading.db import new_id
    feed.set("bulk", "BTC", 100.0)
    feed.set("reya", "BTC", 100.0)
    run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 1.0), None))
    run(e.executor.ioc(Leg("reya", "BTC", "perp", "sell", 1.0), None))
    pid = new_id("pair")
    e.db.insert_pair({"id": pid, "strategy": "funding_arb", "symbol": "BTC", "long_venue": "bulk",
                      "long_kind": "perp", "short_venue": "reya", "short_kind": "perp", "target_usd": 100,
                      "status": "open", "opened_ts": 0, "entry_edge_apr": 0.3, "entry_basis_bp": 0.0, "notes": ""})
    return pid


def test_outage_never_trips_kill_switch_or_closes_positions(eng):  # noqa: F811
    e, feed, _ = eng
    e.update_cfg({"strategies": {"funding_arb": {"max_hold_h": 1}}})   # pair is overdue: would exit
    pid = _open_pair(e, feed)
    run(e.risk.run())                                   # healthy pass sets the day's start equity
    start = e.risk.day_start_equity
    assert start and not e.cfg()["kill_switch"]
    # the network drops: Reya's account and positions cannot be read, its prices go stale
    down = DownBroker(e.brokers["reya"])
    e.brokers["reya"] = down
    feed.ages["reya"] = 999
    for _ in range(3):
        run(e.risk.run())
        e.strategies["funding_arb"].last_eval = 0
        run(e.strategies["funding_arb"]._exits())
    assert not e.cfg()["kill_switch"], "an outage must never look like a daily loss"
    assert not e.guard.daily_loss_hit
    assert e.db.q("SELECT status FROM pairs WHERE id=?", (pid,))[0]["status"] == "open"
    assert run(e.position("bulk", "BTC", "perp")).size == pytest.approx(1.0)   # long leg untouched
    assert "reya" in e.unreachable and "account not readable" in e.risk.state["paused"]
    assert run(e.total_equity()) is None              # never a partial sum
    # back online, but inside the settle period: still hands off
    down.down, feed.ages["reya"] = False, 1
    run(e.risk.run())
    assert "reya" not in e.unreachable and not e.settled("reya")
    run(e.strategies["funding_arb"]._exits())
    assert e.db.q("SELECT status FROM pairs WHERE id=?", (pid,))[0]["status"] == "open"
    # settled: normal rules apply again (the overdue pair is closed by its max-hold exit)
    e.last_unreachable["reya"] -= e.SETTLE_S + 1
    assert e.settled("reya")
    run(e.strategies["funding_arb"]._exits())
    assert e.db.q("SELECT status FROM pairs WHERE id=?", (pid,))[0]["status"] in ("closing", "closed")
    assert not e.cfg()["kill_switch"]


def test_unavailable_connector_is_not_read_as_flat(eng):  # noqa: F811
    from it45.trading.engine import Unavailable
    e, feed, _ = eng
    pid = _open_pair(e, feed)
    e.brokers["reya"] = Unavailable("reya", "testnet", "started offline")
    with pytest.raises(RuntimeError):
        run(e.brokers["reya"].positions())
    for _ in range(3):
        run(e.risk.run())
    assert e.db.q("SELECT status FROM pairs WHERE id=?", (pid,))[0]["status"] == "open"
    assert run(e.position("bulk", "BTC", "perp")).size == pytest.approx(1.0)


def test_real_loss_still_trips_daily_brake(eng):  # noqa: F811
    e, feed, _ = eng
    e.update_cfg({"caps": {"max_order_usd": 2000, "max_venue_usd": 2000}})
    feed.set("bulk", "BTC", 100.0)
    assert run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 10.0), None)).filled == 10.0
    run(e.risk.run())
    feed.set("bulk", "BTC", 85.0)                      # -$150 on $5,000 capital: beyond 3 %
    run(e.risk.run())
    assert e.guard.daily_loss_hit and e.cfg()["kill_switch"]


def test_stale_prices_alone_keep_daily_brake_armed(eng):  # noqa: F811
    e, feed, _ = eng
    e.update_cfg({"caps": {"max_order_usd": 2000, "max_venue_usd": 2000}})
    feed.set("bulk", "BTC", 100.0)
    feed.set("arcus", "BTC", 100.0)
    run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 10.0), None))
    run(e.risk.run())
    feed.ages["reya"] = 999                    # one venue's public API is down; accounts all readable
    feed.set("bulk", "BTC", 85.0)              # a real loss elsewhere
    run(e.risk.run())
    assert "reya" in e.unreachable
    assert e.guard.daily_loss_hit, "a venue outage must not switch off protection against real losses"
