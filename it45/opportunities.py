"""Opportunity engine -- pure functions over the latest quotes (no I/O, fully testable).

Cross-venue funding arb, for one symbol and an ordered venue pair (long L, short S):
    edge_1h   = funding_S - funding_L          (short RECEIVES f_S, long PAYS f_L)
    cost      = 2 * (fee_L + fee_S)            (open + close on both legs)
    net(H)    = edge_1h * H - cost             (hold H hours at TODAY's rates)
    net_apr   = net(H) / H * 8760
    breakeven = cost / edge_1h hours
    basis     = (px_S - px_L) / px_mid         (reported, NOT added to net: it is
                                               mark-to-market risk, not carry)

Hyperliquid spot-perp carry: long spot, short perp on the same venue:
    edge_1h = funding_perp ; cost = 2 * (fee_spot + fee_perp)

IMPORTANT: today's funding is not a forecast of the next H hours. Every number
here is a "if rates stay where they are" figure -- M2 adds funding persistence
measured from the stored history before anything is allowed to trade on it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from .models import HOURS_PER_YEAR, Quote

MAX_BASIS_BP = 500   # legs priced >5% apart are treated as different contracts
WARN_BASIS_BP = 50


@dataclass
class Opportunity:
    kind: str            # "cross" | "carry"
    symbol: str
    long_venue: str
    short_venue: str
    long_leg: str        # "perp" | "spot"
    long_funding_apr: float
    short_funding_apr: float
    edge_apr: float      # gross funding spread, annualised
    cost_bp: float       # round-trip fees, both legs, basis points
    net_apr: float       # over the horizon, after cost
    net_bp: float        # net over the horizon in basis points of notional
    breakeven_h: float | None
    basis_bp: float | None
    min_volume_usd: float
    flags: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _fee(q: Quote, mode: str) -> float:
    fee = q.maker_fee if mode == "maker" else q.taker_fee
    return fee or 0.0


def _flags(*qs: Quote) -> list[str]:
    out = []
    for q in qs:
        if q.venue == "meridian" and q.category in ("index", "commodity"):
            out.append("meridian mPerp: position fees not modelled")
        if q.venue == "arcus" and q.category in ("equity", "index", "commodity"):
            out.append("arcus RWA: off-hours funding/price bands")
        if (q.volume_24h_usd or 0) < 250_000:
            out.append(f"thin: {q.venue} 24h vol < $250k")
    return out


def _make(kind, sym, L: Quote, S: Quote, mode, horizon_h) -> Opportunity | None:
    if L.funding_1h is None and L.kind == "perp":
        return None
    if S.funding_1h is None:
        return None
    fl = L.funding_1h if L.kind == "perp" else 0.0
    edge = S.funding_1h - fl
    cost = 2 * (_fee(L, mode) + _fee(S, mode))
    net = edge * horizon_h - cost
    pl, ps = L.px, S.px
    basis = ((ps - pl) / ((ps + pl) / 2) * 1e4) if pl and ps else None
    flags = _flags(L, S)
    if basis is not None and abs(basis) > MAX_BASIS_BP:
        return None  # almost surely different contracts (e.g. kPEPE vs PEPE), not an arb
    if basis is not None and abs(basis) > WARN_BASIS_BP:
        flags.append(f"price gap {basis:+.0f} bp between legs")
    if abs(edge) * HOURS_PER_YEAR > 1.0:
        flags.append("extreme rate (>100%/yr): usually transient, verify before trusting")
    return Opportunity(
        kind=kind, symbol=sym, long_venue=L.venue, short_venue=S.venue, long_leg=L.kind,
        long_funding_apr=fl * HOURS_PER_YEAR, short_funding_apr=S.funding_1h * HOURS_PER_YEAR,
        edge_apr=edge * HOURS_PER_YEAR, cost_bp=cost * 1e4,
        net_apr=net / horizon_h * HOURS_PER_YEAR, net_bp=net * 1e4,
        breakeven_h=(cost / edge) if edge > 0 else None,
        basis_bp=basis,
        min_volume_usd=min(L.volume_24h_usd or 0, S.volume_24h_usd or 0),
        flags=flags,
    )


def cross_venue(quotes: list[Quote], horizon_h: float = 72, mode: str = "maker",
                min_volume_usd: float = 0) -> list[Opportunity]:
    by_sym: dict[str, list[Quote]] = defaultdict(list)
    for q in quotes:
        if q.kind == "perp" and q.status == "trading" and q.funding_1h is not None and q.px:
            by_sym[q.symbol].append(q)
    out = []
    for sym, qs in by_sym.items():
        for i, a in enumerate(qs):
            for b in qs[i + 1:]:
                if a.venue == b.venue:
                    continue
                # orient so the short leg is the one with the higher funding
                L, S = (a, b) if b.funding_1h >= a.funding_1h else (b, a)
                o = _make("cross", sym, L, S, mode, horizon_h)
                if o and o.min_volume_usd >= min_volume_usd:
                    out.append(o)
    out.sort(key=lambda o: o.net_apr, reverse=True)
    return out


def spot_carry(quotes: list[Quote], horizon_h: float = 72, mode: str = "maker",
               min_volume_usd: float = 0, venue: str = "hyperliquid") -> list[Opportunity]:
    spot = {q.symbol: q for q in quotes if q.venue == venue and q.kind == "spot" and q.px}
    out = []
    for p in quotes:
        if p.venue != venue or p.kind != "perp" or p.symbol not in spot:
            continue
        if p.funding_1h is None or p.funding_1h <= 0:
            continue  # negative funding would need a spot short (borrow) -- not in v1
        o = _make("carry", p.symbol, spot[p.symbol], p, mode, horizon_h)
        if o and o.min_volume_usd >= min_volume_usd:
            out.append(o)
    out.sort(key=lambda o: o.net_apr, reverse=True)
    return out


def matrix(quotes: list[Quote], min_venues: int = 2) -> list[dict]:
    """One row per perp symbol listed on >= min_venues venues."""
    by_sym: dict[str, dict[str, Quote]] = defaultdict(dict)
    for q in quotes:
        if q.kind == "perp":
            by_sym[q.symbol][q.venue] = q
    rows = []
    for sym, vq in by_sym.items():
        if len(vq) < min_venues:
            continue
        fr = [q.funding_apr for q in vq.values() if q.funding_apr is not None]
        px = [q.px for q in vq.values() if q.px]
        rows.append({
            "symbol": sym,
            "category": next(iter(vq.values())).category,
            "venues": {v: {"px": q.px, "funding_apr": q.funding_apr,
                           "volume_24h_usd": q.volume_24h_usd, "status": q.status,
                           "raw_symbol": q.raw_symbol} for v, q in vq.items()},
            "spread_apr": (max(fr) - min(fr)) if len(fr) >= 2 else None,
            "px_dispersion_bp": ((max(px) - min(px)) / (sum(px) / len(px)) * 1e4)
                                if len(px) >= 2 else None,
        })
    rows.sort(key=lambda r: (-(len(r["venues"])), -(r["spread_apr"] or 0)))
    return rows
