import pytest

from it45 import opportunities as opp
from it45.models import HOURS_PER_YEAR, Quote
from it45.venues.base import norm_symbol
from it45.venues.reya import reya_base


def q(venue, sym, fr, px=100.0, kind="perp", maker=0.0, taker=0.0003, vol=1e7, **kw):
    return Quote(venue=venue, symbol=sym, raw_symbol=sym, kind=kind, mid=px, funding_1h=fr,
                 maker_fee=maker, taker_fee=taker, volume_24h_usd=vol, **kw)


def test_cross_orients_short_on_higher_funding():
    rows = opp.cross_venue([q("a", "BTC", 0.00001), q("b", "BTC", 0.00005)], horizon_h=100,
                           mode="taker")
    assert len(rows) == 1
    o = rows[0]
    assert (o.long_venue, o.short_venue) == ("a", "b")
    assert o.edge_apr == pytest.approx(0.00004 * HOURS_PER_YEAR)
    # 4 legs of 3 bp taker = 12 bp; edge over 100h = 40 bp -> net 28 bp
    assert o.cost_bp == pytest.approx(12)
    assert o.net_bp == pytest.approx(28)
    assert o.breakeven_h == pytest.approx(30)


def test_negative_funding_long_receives():
    # long pays a NEGATIVE rate (receives), short receives positive: edge is the sum
    o = opp.cross_venue([q("a", "ETH", -0.00002), q("b", "ETH", 0.00001)], mode="maker")[0]
    assert o.long_venue == "a"
    assert o.edge_apr == pytest.approx(0.00003 * HOURS_PER_YEAR)


def test_maker_mode_uses_maker_fee():
    rows = opp.cross_venue([q("a", "X", 0, maker=0.0001), q("b", "X", 0.0001, maker=0.0002)],
                           horizon_h=10, mode="maker")
    assert rows[0].cost_bp == pytest.approx(6)


def test_same_venue_and_single_listing_ignored():
    assert opp.cross_venue([q("a", "BTC", 0.1), q("a", "ETH", 0.1)]) == []


def test_stale_or_suspended_excluded():
    qs = [q("a", "BTC", 0.0), q("b", "BTC", 0.001, status="suspended")]
    assert opp.cross_venue(qs) == []


def test_min_volume_filter():
    qs = [q("a", "BTC", 0.0, vol=50_000), q("b", "BTC", 0.001)]
    assert opp.cross_venue(qs, min_volume_usd=100_000) == []
    assert len(opp.cross_venue(qs, min_volume_usd=0)) == 1


def test_spot_carry_positive_funding_only():
    qs = [q("hyperliquid", "BTC", None, kind="spot", maker=0.0004, taker=0.0007),
          q("hyperliquid", "BTC", 0.00002, maker=0.00015, taker=0.00045),
          q("hyperliquid", "ETH", None, kind="spot"),
          q("hyperliquid", "ETH", -0.00002)]
    rows = opp.spot_carry(qs, horizon_h=72, mode="maker")
    assert [r.symbol for r in rows] == ["BTC"]
    r = rows[0]
    assert r.long_leg == "spot" and r.long_funding_apr == 0
    assert r.cost_bp == pytest.approx(2 * (4 + 1.5))


def test_basis_sign():
    o = opp.cross_venue([q("a", "BTC", 0.0, px=100), q("b", "BTC", 0.001, px=101)])[0]
    assert o.basis_bp > 0  # short leg is the richer one


def test_symbol_normalisation():
    assert norm_symbol("BTC-USD") == "BTC"
    assert norm_symbol("BTCUSD") == "BTC"
    assert norm_symbol("UBTC") == "BTC"
    assert reya_base("BTCRUSDPERP") == "BTC"
    assert reya_base("HYPERUSDPERP") == "HYPE"


def test_matrix_requires_two_venues():
    rows = opp.matrix([q("a", "BTC", 0.00001), q("b", "BTC", 0.00003), q("a", "DOGE", 0)])
    assert [r["symbol"] for r in rows] == ["BTC"]
    assert rows[0]["spread_apr"] == pytest.approx(0.00002 * HOURS_PER_YEAR)


def test_mismatched_contracts_dropped():
    # e.g. a 1000x-unit listing on one venue: 5%+ apart => not the same contract
    assert opp.cross_venue([q("a", "PEPE", 0.0, px=100), q("b", "PEPE", 0.001, px=110)]) == []
    o = opp.cross_venue([q("a", "BTC", 0.0, px=100), q("b", "BTC", 0.001, px=101)])[0]
    assert any("price gap" in f for f in o.flags)
