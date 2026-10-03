"""M2 trading core against a fake feed: paper fills, fees, funding, guard, executor, brakes."""
import asyncio
import time

import pytest

from it45.models import Quote
from it45.store import Settings, Store
from it45.trading import config as tcfg
from it45.trading.engine import ConfigError, Engine
from it45.trading.executor import Leg
from it45.trading.persistence import persistence


class FakeFeed:
    def __init__(self):
        self.venues = {v: None for v in ("hyperliquid", "arcus", "bulk", "reya", "meridian")}
        self.quotes = {v: [] for v in self.venues}
        self.listeners = []
        self.ages = {v: 1.0 for v in self.venues}

    def set(self, venue, symbol, mid, funding=0.0, kind="perp", maker=0.0, taker=0.0005, vol=1e7, spread=None):
        qs = [q for q in self.quotes[venue] if not (q.symbol == symbol and q.kind == kind)]
        bid = ask = None
        if spread:
            bid, ask = mid * (1 - spread), mid * (1 + spread)
        qs.append(Quote(venue=venue, symbol=symbol, raw_symbol=symbol, kind=kind, mid=mid, bid=bid, ask=ask,
                        funding_1h=funding if kind == "perp" else None, maker_fee=maker, taker_fee=taker,
                        volume_24h_usd=vol, max_leverage=20))
        self.quotes[venue] = qs

    def all_quotes(self, include_stale=True):
        return [q for qs in self.quotes.values() for q in qs]

    def age(self, v):
        return self.ages[v]


@pytest.fixture
def eng(tmp_path):
    feed = FakeFeed()
    store = Store(tmp_path / "t.db")
    settings = Settings(tmp_path / "s.json")
    e = Engine(feed, settings, store)
    e.update_cfg({"exec": {"reprice_s": 1, "maker_attempts": 1}, "brakes": {"imbalance_timeout_s": 1}})
    return e, feed, store


def run(c):
    return asyncio.run(c)


def test_taker_fill_fee_and_cash(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0, taker=0.0005)
    br = e.brokers["bulk"]
    ack = run(br.place(symbol="BTC", kind="perp", side="buy", size=1.0, price=101, post_only=False))
    assert ack.status == "filled"
    assert ack.avg_px == pytest.approx(100.02)           # mid + 2 bp synthetic half spread
    assert ack.fee == pytest.approx(100.02 * 0.0005)
    acc = run(br.account())
    assert acc["cash"] == pytest.approx(1000 - ack.fee)
    pos = run(br.positions())[0]
    assert pos.size == 1.0 and pos.avg_px == pytest.approx(100.02)


def test_post_only_rests_then_fills_when_market_trades_through(eng):
    e, feed, _ = eng
    feed.set("arcus", "ETH", 100.0, maker=0.0)
    br = e.brokers["arcus"]
    assert run(br.place(symbol="ETH", kind="perp", side="buy", size=1, price=100.5, post_only=True)).error \
        == "post-only would cross the book"
    ack = run(br.place(symbol="ETH", kind="perp", side="buy", size=1, price=99.99, post_only=True))
    assert ack.status == "open"
    br.tick(0)
    assert run(br.status(ack.order_id)).status == "open"     # ask 100.02 not below 99.99
    feed.set("arcus", "ETH", 99.9, maker=0.0)                # ask 99.92 < 99.99 -> filled
    br.tick(0)
    st = run(br.status(ack.order_id))
    assert st.status == "filled" and st.avg_px == 99.99 and st.fee == 0


def test_funding_accrual_sign(eng):
    e, feed, _ = eng
    feed.set("reya", "SOL", 100.0, funding=0.0001)           # 1 bp/h, longs pay
    br = e.brokers["reya"]
    run(br.place(symbol="SOL", kind="perp", side="sell", size=10, price=99, post_only=False))
    cash0 = run(br.account())["cash"]
    br.tick(3600)                                           # one hour
    # short receives 10 * 100 * 0.0001 = $0.10
    assert run(br.account())["cash"] - cash0 == pytest.approx(0.10, rel=1e-6)
    assert run(br.positions())[0].funding == pytest.approx(0.10, rel=1e-6)


def test_realized_pnl_on_close(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0, taker=0.0)
    br = e.brokers["bulk"]
    run(br.place(symbol="BTC", kind="perp", side="buy", size=2, price=101, post_only=False))
    feed.set("bulk", "BTC", 110.0, taker=0.0)
    run(br.place(symbol="BTC", kind="perp", side="sell", size=2, price=100, post_only=False, reduce_only=True))
    acc = run(br.account())
    # bought at 100.02, sold at 109.978 -> 2 * 9.958
    assert acc["cash"] - 1000 == pytest.approx(2 * (110 * (1 - 2e-4) - 100 * (1 + 2e-4)))
    assert run(br.positions()) == []


def test_reduce_only_cannot_open_and_spot_cannot_short(eng):
    e, feed, _ = eng
    feed.set("hyperliquid", "BTC", 100.0, kind="spot")
    br = e.brokers["hyperliquid"]
    assert "would increase" in run(br.place(symbol="BTC", kind="spot", side="sell", size=1, price=99,
                                            post_only=False, reduce_only=True)).error
    assert "no spot shorting" in run(br.place(symbol="BTC", kind="spot", side="sell", size=1, price=99,
                                              post_only=False)).error


def test_insufficient_collateral(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0)
    br = e.brokers["bulk"]   # $1000 at 2x -> $2000 notional max
    assert "insufficient" in run(br.place(symbol="BTC", kind="perp", side="buy", size=25, price=101,
                                          post_only=False)).error


def test_guard_caps_and_kill_switch(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0)
    r = run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 3.0), None))     # $300 > $250 per order
    assert r.filled == 0 and "per-order cap" in r.error
    run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 1.0), None))
    e.update_cfg({"kill_switch": True})
    r = run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 1.0), None))
    assert "Kill switch" in r.error
    r = run(e.executor.ioc(Leg("bulk", "BTC", "perp", "sell", 1.0, True), None))
    assert r.filled == pytest.approx(1.0)                                       # closing still allowed


def test_stale_venue_halts_new_orders(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0)
    feed.ages["bulk"] = 999
    run(e.risk.run())
    assert "bulk" in e.guard.halted
    r = run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 1.0), None))
    assert "halted" in r.error
    feed.ages["bulk"] = 1
    run(e.risk.run())
    assert "bulk" not in e.guard.halted


def test_pair_open_taker_fallback_and_close(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0, funding=0.0)
    feed.set("reya", "BTC", 100.0, funding=0.0001)
    legs = [Leg("bulk", "BTC", "perp", "buy", 2.0), Leg("reya", "BTC", "perp", "sell", 2.0)]
    ra, rb = run(e.executor.run_pair(legs, "p1"))       # maker never fills (static book) -> IOC
    assert ra.filled == pytest.approx(2.0) and rb.filled == pytest.approx(2.0)
    assert run(e.position("bulk", "BTC", "perp")).size == pytest.approx(2.0)
    assert run(e.position("reya", "BTC", "perp")).size == pytest.approx(-2.0)


def test_imbalance_repair_trims_heavy_leg(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0)
    feed.set("reya", "BTC", 100.0)
    e.update_cfg({"venue_modes": {"reya": "off"}})          # short leg will be refused
    legs = [Leg("bulk", "BTC", "perp", "buy", 2.0), Leg("reya", "BTC", "perp", "sell", 2.0)]
    ra, rb = run(e.executor.run_pair(legs, "p2"))
    assert rb.filled == 0
    pos = run(e.position("bulk", "BTC", "perp"))
    assert pos is None or abs(pos.size) < 1e-9             # lone long was trimmed away


def test_config_validation(eng):
    e, *_ = eng
    with pytest.raises(ConfigError):
        e.update_cfg({"leverage": 11})
    with pytest.raises(ConfigError):
        e.update_cfg({"venue_modes": {"arcus": "testnet"}})       # no Arcus testnet connector yet
    with pytest.raises(ConfigError):
        e.update_cfg({"venue_modes": {"bulk": "mainnet"}})
    with pytest.raises(ConfigError):
        e.update_cfg({"strategies": {"funding_arb": {"exit_edge_apr": 0.5}}})


def test_mode_switch_refused_with_open_position(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0)
    run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 1.0), None))
    with pytest.raises(ConfigError, match="close its positions"):
        e.update_cfg({"venue_modes": {"bulk": "off"}})


def test_kill_flattens_everything(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0)
    feed.set("reya", "BTC", 100.0)
    run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 1.0), None))
    run(e.executor.ioc(Leg("reya", "BTC", "perp", "sell", 1.0), None))
    out = run(e.kill("test"))
    assert out["positions_left"] == []
    assert e.cfg()["kill_switch"] is True


def test_persistence_requires_history(eng):
    e, feed, store = eng
    now = time.time()
    rows = []
    for i in range(120):   # two hours, edge 1bp/h for 3/4 of the samples
        t = now - 7200 + i * 60
        rows.append(Quote(venue="bulk", symbol="BTC", raw_symbol="BTC", funding_1h=0.0, mid=1, ts=t))
        rows.append(Quote(venue="reya", symbol="BTC", raw_symbol="BTC",
                          funding_1h=0.0001 if i % 4 else -0.0001, mid=1, ts=t))
    store.save_snapshot(rows)
    p = persistence(store, "BTC", "bulk", "perp", "reya", 6)
    assert p.n == 120
    assert p.frac_positive == pytest.approx(0.75)
    assert p.mean_edge_apr == pytest.approx(0.00005 * 8760)


def test_defaults_are_valid():
    assert tcfg.validate(tcfg.merged({})) == []


def _history(store, edge, hours=6, venues=("bulk", "reya")):
    now = time.time()
    rows = []
    for i in range(int(hours * 60)):
        t = now - hours * 3600 + i * 60
        rows.append(Quote(venue=venues[0], symbol="BTC", raw_symbol="BTC", funding_1h=0.0, mid=1, ts=t))
        rows.append(Quote(venue=venues[1], symbol="BTC", raw_symbol="BTC", funding_1h=edge, mid=1, ts=t))
    store.save_snapshot(rows)


def test_strategy_opens_persistent_pair_and_exits_when_edge_fades(eng):
    e, feed, store = eng
    edge = 0.30 / 8760                                   # 30%/yr
    feed.set("bulk", "BTC", 100.0, funding=0.0, taker=0.0001)   # 4 legs x 1 bp: breakeven 11.7 h
    feed.set("reya", "BTC", 100.0, funding=edge, taker=0.0001)
    _history(store, edge)
    e.update_cfg({"strategies": {"funding_arb": {"enabled": True, "eval_s": 0}}})
    s = e.strategies["funding_arb"]
    run(s.evaluate())
    pairs = e.db.open_pairs()
    assert len(pairs) == 1 and pairs[0]["status"] == "open", s.last_note
    p = pairs[0]
    assert (p["long_venue"], p["short_venue"]) == ("bulk", "reya")
    # the same legs cannot be reused by a second pair
    run(s.evaluate())
    assert len(e.db.open_pairs()) == 1
    # edge collapses for the last hour -> exit below 3%/yr
    store.db.execute("DELETE FROM snapshots WHERE ts >= ?", (time.time() - 3600,))
    _history(store, 0.0, hours=1)
    feed.set("reya", "BTC", 100.0, funding=0.0, taker=0.0001)
    s.last_eval = 0
    run(s.evaluate())
    closed = e.db.q("SELECT * FROM pairs WHERE id=?", (p["id"],))[0]
    assert closed["status"] == "closed" and "below exit" in closed["close_reason"]
    assert run(e.position("bulk", "BTC", "perp")) is None


def test_strategy_refuses_spread_without_history(eng):
    e, feed, store = eng
    feed.set("bulk", "BTC", 100.0, funding=0.0, taker=0.0001)
    feed.set("reya", "BTC", 100.0, funding=0.5 / 8760, taker=0.0001)
    e.update_cfg({"strategies": {"funding_arb": {"enabled": True, "eval_s": 0}}})
    s = e.strategies["funding_arb"]
    run(s.evaluate())
    assert e.db.open_pairs() == []
    assert "persistence" in s.last_note
    assert "samples" in s.last_candidates[0]["why_not"]


def test_preflight_refuses_whole_pair_without_trading_either_leg(eng):
    e, feed, _ = eng
    feed.set("bulk", "BTC", 100.0)
    feed.set("reya", "BTC", 100.0)
    e.update_cfg({"caps": {"max_venue_usd": 150}})
    run(e.executor.ioc(Leg("bulk", "BTC", "perp", "buy", 1.0), None))          # bulk now $100 gross
    legs = [Leg("bulk", "BTC", "perp", "buy", 1.0), Leg("reya", "BTC", "perp", "sell", 1.0)]
    ra, rb = run(e.executor.run_pair(legs, "p3"))
    assert ra.filled == 0 and rb.filled == 0 and "bulk" in ra.error
    assert run(e.position("reya", "BTC", "perp")) is None                    # nothing sent to reya
    assert e.db.q("SELECT COUNT(*) n FROM orders WHERE venue='reya'")[0]["n"] == 0


def test_same_venue_legs_count_together_in_preflight(eng):
    e, feed, _ = eng
    feed.set("hyperliquid", "ETH", 100.0, kind="spot")
    feed.set("hyperliquid", "ETH", 100.0)
    e.update_cfg({"caps": {"max_venue_usd": 300}})
    legs = [Leg("hyperliquid", "ETH", "spot", "buy", 2.0), Leg("hyperliquid", "ETH", "perp", "sell", 2.0)]
    ra, rb = run(e.executor.run_pair(legs, "p4"))                             # $200 + $200 > $300
    assert ra.filled == 0 and rb.filled == 0 and "gross" in ra.error


def test_kill_reason_recorded_on_pairs(eng):
    e, feed, store = eng
    edge = 0.30 / 8760
    feed.set("bulk", "BTC", 100.0, funding=0.0, taker=0.0001)
    feed.set("reya", "BTC", 100.0, funding=edge, taker=0.0001)
    _history(store, edge)
    e.update_cfg({"strategies": {"funding_arb": {"enabled": True, "eval_s": 0}}})
    run(e.strategies["funding_arb"].evaluate())
    pid = e.db.open_pairs()[0]["id"]
    run(e.kill("test"))
    p = e.db.q("SELECT * FROM pairs WHERE id=?", (pid,))[0]
    assert p["status"] == "closed" and p["close_reason"].startswith("kill switch")


def test_capped_candidate_is_skipped_without_a_pair_row(eng):
    e, feed, store = eng
    edge = 0.30 / 8760
    feed.set("bulk", "BTC", 100.0, funding=0.0, taker=0.0001)
    feed.set("reya", "BTC", 100.0, funding=edge, taker=0.0001)
    _history(store, edge)
    e.update_cfg({"caps": {"max_gross_usd": 100}, "strategies": {"funding_arb": {"enabled": True, "eval_s": 0}}})
    s = e.strategies["funding_arb"]
    run(s.evaluate())
    assert e.db.q("SELECT COUNT(*) n FROM pairs")[0]["n"] == 0
    assert "skipped" in s.last_note
    assert e.db.q("SELECT COUNT(*) n FROM orders")[0]["n"] == 0
