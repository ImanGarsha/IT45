"""Hyperliquid testnet broker with a fake SDK: rounding, tif mapping, response parsing, and that
nothing but order/cancel/update_leverage can ever be called on the Exchange."""
import asyncio

import pytest

from it45.trading.db import TradeDB
from it45.store import Store
from it45.trading.hl_testnet import HyperliquidTestnet, round_px, round_sz


class FakeInfo:
    def meta(self):
        return {"universe": [{"name": "BTC", "szDecimals": 5}, {"name": "kPEPE", "szDecimals": 0}]}

    def spot_meta(self):
        return {"tokens": [{"index": 0, "name": "USDC", "szDecimals": 8},
                           {"index": 1, "name": "UBTC", "szDecimals": 5}],
                "universe": [{"name": "@142", "tokens": [1, 0]}]}

    def l2_snapshot(self, coin):
        return {"levels": [[{"px": "100.0"}], [{"px": "100.2"}]]}

    def user_fills(self, addr):
        return [{"oid": 7, "sz": "0.5", "px": "100.1", "fee": "0.02"}]

    def query_order_by_oid(self, addr, oid):
        return {"order": {"status": "filled"}}

    def open_orders(self, addr):
        return []

    def user_state(self, addr):
        return {"assetPositions": [{"position": {"coin": "BTC", "szi": "-0.5", "entryPx": "100",
                                                 "positionValue": "50", "unrealizedPnl": "0",
                                                 "liquidationPx": "150", "cumFunding": {"sinceOpen": "-0.1"}}}],
                "marginSummary": {"accountValue": "1000", "totalMarginUsed": "5"}, "withdrawable": "990"}

    def spot_user_state(self, addr):
        return {"balances": [{"coin": "USDC", "total": "10"}]}


class FakeExchange:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def order(self, *a, **k):
        self.calls.append(("order", a, k))
        return self.reply

    def cancel(self, *a):
        self.calls.append(("cancel", a, {}))
        return {"status": "ok"}

    def withdraw_from_bridge(self, *a):
        raise AssertionError("must never be reachable")

    def usd_transfer(self, *a):
        raise AssertionError("must never be reachable")


def make(tmp_path, reply):
    ex = FakeExchange(reply)
    b = HyperliquidTestnet(TradeDB(Store(tmp_path / "t.db")), {"IT45_HL_ACCOUNT_ADDRESS": "0xabc"},
                           info=FakeInfo(), exchange=ex)
    return b, ex


def test_rounding_rules():
    assert round_px(84567.123, 5, False) == 84567.0       # 5 significant figures
    assert round_px(0.0123456, 0, False) == 0.012346
    assert round_sz(0.123456789, 5) == pytest.approx(0.12345)


def test_post_only_maps_to_alo_and_rests(tmp_path):
    b, ex = make(tmp_path, {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 7}}]}}})
    ack = asyncio.run(b.place(symbol="BTC", kind="perp", side="buy", size=0.5, price=100.0, post_only=True))
    assert ack.status == "open"
    name, args, kw = ex.calls[0]
    assert args[0] == "BTC" and args[1] is True and args[4] == {"limit": {"tif": "Alo"}}
    st = asyncio.run(b.status(ack.order_id))
    assert st.status == "filled" and st.filled == 0.5 and st.avg_px == pytest.approx(100.1)


def test_ioc_fill_and_spot_name_mapping(tmp_path):
    b, ex = make(tmp_path, {"status": "ok", "response": {"data": {"statuses": [
        {"filled": {"oid": 9, "totalSz": "0.1", "avgPx": "100.2"}}]}}})
    ack = asyncio.run(b.place(symbol="BTC", kind="spot", side="buy", size=0.1, price=100.3, post_only=False))
    assert ack.status == "filled" and ack.filled == 0.1
    assert ex.calls[0][1][0] == "@142" and ex.calls[0][1][4] == {"limit": {"tif": "Ioc"}}


def test_post_only_cross_error_normalised(tmp_path):
    b, _ = make(tmp_path, {"status": "ok", "response": {"data": {"statuses": [
        {"error": "Post only order would have immediately matched, bbo was 100.0@100.2"}]}}})
    ack = asyncio.run(b.place(symbol="BTC", kind="perp", side="buy", size=0.5, price=100.2, post_only=True))
    assert ack.status == "rejected" and ack.error == "post-only would cross the book"


def test_unlisted_symbol_rejected(tmp_path):
    b, ex = make(tmp_path, {})
    ack = asyncio.run(b.place(symbol="DOGE", kind="perp", side="buy", size=1, price=1, post_only=True))
    assert ack.status == "rejected" and "not listed" in ack.error and ex.calls == []


def test_exchange_surface_is_restricted(tmp_path):
    b, _ = make(tmp_path, {})
    with pytest.raises(PermissionError):
        b.ex.withdraw_from_bridge(1, "0x0")
    with pytest.raises(PermissionError):
        b.ex.usd_transfer(1, "0x0")


def test_positions_and_liquidation_distance(tmp_path):
    b, _ = make(tmp_path, {})
    p = asyncio.run(b.positions())[0]
    assert (p.symbol, p.size, p.mark) == ("BTC", -0.5, 100.0)
    assert p.liq_distance == pytest.approx(0.5)
    assert p.funding == pytest.approx(0.1)


def test_missing_keys_refuse_to_start(tmp_path):
    with pytest.raises(RuntimeError, match="IT45_HL_AGENT_KEY"):
        HyperliquidTestnet(TradeDB(Store(tmp_path / "t.db")), {})
