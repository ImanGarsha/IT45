"""Stream parsers fed with frames recorded live on 2026-10-03, and the paper queue model."""
import asyncio
import time

import pytest

from it45.models import Quote
from it45.store import Settings, Store
from it45.streams import StreamHub, Trade
from it45.trading.brokers import PaperBroker
from it45.trading.config import merged
from it45.trading.db import TradeDB


class FakeFeed:
    def __init__(self):
        self.quotes = {v: [] for v in ("hyperliquid", "arcus", "bulk", "reya", "meridian")}
        raws = {"hyperliquid": "BTC", "arcus": "BTC-USD", "bulk": "BTC-USD", "reya": "BTCRUSDPERP",
                "meridian": "BTC-USD"}
        for v, r in raws.items():
            self.quotes[v].append(Quote(venue=v, symbol="BTC", raw_symbol=r, mid=84700.0, maker_fee=0.0,
                                        taker_fee=0.0003, volume_24h_usd=1e7, max_leverage=20))


@pytest.fixture
def hub():
    h = StreamHub(FakeFeed())
    h._meridian_tickers = {"BTC": "BTCUSD"}
    h.got = []
    h.listeners.append(h.got.append)
    return h


def test_hyperliquid_bbo_and_trades(hub):
    s = hub.streams["hyperliquid"]
    s.handle({"channel": "bbo", "data": {"coin": "BTC", "time": 1791038708641, "bbo": [
        {"px": "84719.0", "sz": "6.40495", "n": 33}, {"px": "84720.0", "sz": "1.1658", "n": 15}]}})
    t = hub.top("hyperliquid", "BTC")
    assert (t.bid, t.bid_sz, t.ask) == (84719.0, 6.40495, 84720.0)
    s.handle({"channel": "trades", "data": [{"coin": "BTC", "side": "B", "px": "84720.0", "sz": "0.00531",
                                             "time": 1791038680275}]})
    assert hub.got[-1].taker_buy is True and hub.got[-1].px == 84720.0


def test_arcus_bbo_ack_and_trades(hub):
    s = hub.streams["arcus"]
    s.handle({"type": "subscribed", "channel": "bbo", "id": "BTC-USD", "contents": {
        "bestBid": {"price": "84708.8", "size": "0.07536583"}, "bestAsk": {"price": "84708.9", "size": "0.0157573"},
        "timestamp": 1791038680682620}})
    assert hub.top("arcus", "BTC").ask == 84708.9
    s.handle({"type": "channel_data", "channel": "trades", "id": "BTC-USD", "contents": [
        {"side": "SELL", "price": "84708.8", "size": "0.00059026", "timestamp": 1791038684857739}]})
    assert hub.got[-1].taker_buy is False and hub.got[-1].ts == pytest.approx(1791038684.857739)


def test_bulk_snapshot_and_trades(hub):
    s = hub.streams["bulk"]
    s.handle({"type": "l2Snapshot", "data": {"book": {"updateType": "snapshot", "symbol": "BTC-USD", "levels": [
        [{"px": 84720.82, "sz": 0.0682056, "n": 2}], [{"px": 84720.83, "sz": 0.5, "n": 1}]],
        "timestamp": 1791038680521102828}}, "topic": "l2snapshot.BTC-USD.1"})
    assert hub.top("bulk", "BTC").bid == 84720.82
    s.handle({"type": "trades", "data": {"trades": [{"s": "BTC-USD", "px": 84720.83, "sz": 0.005049,
                                                     "time": 1791038744784, "side": False}]}})
    assert hub.got[-1].taker_buy is False


def test_reya_depth_snapshot_then_deltas(hub):
    s = hub.streams["reya"]
    s.handle({"type": "subscribed", "channel": "/v2/market/BTCRUSDPERP/depth", "contents": {
        "symbol": "BTCRUSDPERP", "type": "SNAPSHOT",
        "bids": [{"px": "84745.76", "qty": "0.0023"}, {"px": "84745.759", "qty": "0.3716"}],
        "asks": [{"px": "84754.991", "qty": "0.5"}, {"px": "84755.395", "qty": "1"}]}})
    assert (hub.top("reya", "BTC").bid, hub.top("reya", "BTC").ask) == (84745.76, 84754.991)
    s.handle({"type": "channel_data", "channel": "/v2/market/BTCRUSDPERP/depth", "data": {
        "symbol": "BTCRUSDPERP", "type": "UPDATE", "bids": [{"px": "84745.76", "qty": "0"}],
        "asks": [{"px": "84750.0", "qty": "0.2"}]}})
    t = hub.top("reya", "BTC")
    assert (t.bid, t.bid_sz, t.ask, t.ask_sz) == (84745.759, 0.3716, 84750.0, 0.2)
    s.handle({"type": "channel_data", "channel": "/v2/market/BTCRUSDPERP/perpExecutions",
              "data": [{"price": "84749.8", "qty": "0.0017", "side": "A", "timestamp": 1791038020000}]})
    assert hub.got[-1].taker_buy is False and hub.got[-1].sz == 0.0017


def test_meridian_ticker_and_fills(hub):
    s = hub.streams["meridian"]
    s.handle({"e": "Ticker", "t": 1791038681136, "data": {"s": "BTCUSD", "bidPx": "84712", "askPx": "84729",
                                                         "bidAmt": "0.53115", "askAmt": "0.53115"}})
    assert hub.top("meridian", "BTC").ask == 84729.0
    s.handle({"e": "TradeFill", "t": 1, "data": {"s": "BTCUSD", "t": 1791038681136,
                                                  "d": [{"px": "84712", "sz": "0.01", "sd": 1}]}})
    assert hub.got[-1].taker_buy is False


def test_stale_top_is_ignored(hub):
    s = hub.streams["bulk"]
    s.set_top("BTC", 1, 1, 2, 1)
    s.tops["BTC"].ts = time.time() - 60
    assert hub.top("bulk", "BTC") is None


# ---------------------------------------------------------------- queue model
@pytest.fixture
def paper(tmp_path, hub):
    st = Store(tmp_path / "t.db")
    cfg = merged({})
    br = PaperBroker("bulk", hub.feed, TradeDB(st), lambda: cfg, hub=hub)
    hub.streams["bulk"].set_top("BTC", 84700.0, 0.30, 84700.5, 0.2)   # 0.30 BTC already bid
    return br


def _trade(px, sz, taker_buy=False):
    return Trade("bulk", "BTC", px, sz, taker_buy, time.time())


def test_joining_the_bid_waits_for_the_queue(paper):
    a = asyncio.run(paper.place(symbol="BTC", kind="perp", side="buy", size=0.002, price=84700.0, post_only=True))
    assert a.status == "open" and paper.resting[a.order_id].queue_ahead == pytest.approx(0.30)
    paper.on_trade(_trade(84700.0, 0.20))                 # queue 0.30 -> 0.10: still waiting
    assert asyncio.run(paper.status(a.order_id)).status == "open"
    paper.on_trade(_trade(84700.0, 0.05, taker_buy=True))  # a BUY cannot hit our bid
    assert asyncio.run(paper.status(a.order_id)).status == "open"
    paper.on_trade(_trade(84700.0, 0.15))                 # queue exhausted -> we fill, maker fee
    st = asyncio.run(paper.status(a.order_id))
    assert st.status == "filled" and st.avg_px == 84700.0 and st.fee == 0.0


def test_trade_through_fills_immediately(paper):
    a = asyncio.run(paper.place(symbol="BTC", kind="perp", side="buy", size=0.002, price=84700.0, post_only=True))
    paper.on_trade(_trade(84699.5, 0.001))
    assert asyncio.run(paper.status(a.order_id)).status == "filled"


def test_inside_the_spread_has_no_queue(paper):
    a = asyncio.run(paper.place(symbol="BTC", kind="perp", side="sell", size=0.002, price=84700.4, post_only=True))
    assert paper.resting[a.order_id].queue_ahead == 0
    paper.on_trade(_trade(84700.4, 0.0001, taker_buy=True))
    assert asyncio.run(paper.status(a.order_id)).status == "filled"
