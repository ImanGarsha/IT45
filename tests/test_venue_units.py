"""Adapters parse recorded venue payloads into the shared units (fraction per hour)."""
import asyncio

import httpx
import pytest

from it45.venues import VENUES


class FakeClient:
    def __init__(self, routes):
        self.routes = routes

    async def get(self, url, params=None, **kw):
        return self._resp(url)

    async def post(self, url, json=None, **kw):
        return self._resp(url + "#" + json["type"])

    def _resp(self, key):
        for k, v in self.routes.items():
            if key.endswith(k):
                return httpx.Response(200, json=v, request=httpx.Request("GET", "http://x"))
        raise AssertionError(f"unexpected call {key}")


def run(venue, routes):
    return asyncio.run(VENUES[venue](FakeClient(routes)).fetch())


def test_reya_percent_per_hour_is_divided_by_100():
    qs = run("reya", {
        "/perpMarkets/summary": [{"symbol": "BTCRUSDPERP", "fundingRate": "0.00125",
                                  "markPrice": "100", "oraclePrice": "100",
                                  "throttledMidPrice": "100", "volume24h": "1", "oiQty": "1"}],
        "/perpMarketDefinitions": [{"symbol": "BTCRUSDPERP", "maxLeverage": 25}],
    })
    assert qs[0].symbol == "BTC"
    assert qs[0].funding_1h == pytest.approx(0.0000125)   # 0.01%/8h base, per hour
    assert qs[0].funding_apr == pytest.approx(0.1095)


def test_bulk_skips_suspended_and_reads_fraction():
    qs = run("bulk", {
        "/exchangeInfo": [{"symbol": "BTC-USD", "status": "TRADING", "maxLeverage": 40},
                          {"symbol": "AAVE-USD", "status": "SUSPENDED"}],
        "/ticker/BTC-USD": {"lastPrice": 100, "markPrice": 100, "oraclePrice": 100,
                            "fundingRate": 0.0000125, "quoteVolume": 5, "openInterest": 1},
    })
    assert [q.symbol for q in qs] == ["BTC"]
    assert qs[0].funding_1h == pytest.approx(0.0000125)


def test_meridian_uses_base_token_and_product_fees():
    qs = run("meridian", {
        "/v1/product": {"data": [{"id": "u1", "ticker": "XAUXAUUSD", "displayTicker": "XAU-USD",
                                  "baseTokenName": "XAU", "status": "ACTIVE",
                                  "fundingRate1h": "0.0001", "makerFee": "0",
                                  "takerFee": "0.00005", "volume24h": "2",
                                  "openInterest": "2", "maxLeverage": 10}],
                        "hasNext": False},
        "/v1/product/market-price": {"data": [{"productId": "u1", "bestBidPrice": "99",
                                               "bestAskPrice": "101", "oraclePrice": "100"}]},
    })
    q = qs[0]
    assert (q.symbol, q.category, q.mid, q.taker_fee) == ("XAU", "commodity", 100, 0.00005)


def test_hyperliquid_perp_and_usdc_spot():
    qs = run("hyperliquid", {
        "#metaAndAssetCtxs": [{"universe": [{"name": "BTC", "maxLeverage": 40}]},
                              [{"funding": "0.0000125", "markPx": "100", "midPx": "100",
                                "oraclePx": "100", "impactPxs": ["99", "101"],
                                "dayNtlVlm": "5", "openInterest": "1"}]],
        "#spotMetaAndAssetCtxs": [
            {"tokens": [{"index": 0, "name": "USDC"}, {"index": 1, "name": "UBTC"}],
             "universe": [{"name": "@142", "tokens": [1, 0]}]},
            [{"coin": "@142", "midPx": "100", "markPx": "100", "dayNtlVlm": "5"}]],
    })
    assert {(q.symbol, q.kind) for q in qs} == {("BTC", "perp"), ("BTC", "spot")}


def test_hyper_alias_is_reya_only():
    from it45.venues.base import norm_symbol
    from it45.venues.reya import reya_base
    assert reya_base("HYPERUSDPERP") == "HYPE"
    assert norm_symbol("HYPER") == "HYPER"      # Hyperliquid's HYPER is a different token
    assert norm_symbol("HYPE") == "HYPE"
