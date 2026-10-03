"""Spread persistence from stored snapshots.

Today's funding spread is not a forecast. Before a strategy may open a pair it must show that
the spread has been there for a while: over `lookback_h` the mean edge must clear the entry
threshold AND be positive in at least `min_frac_positive` of the samples.

Snapshots are written per venue at slightly different instants within a cycle, so samples are
aligned on the minute.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..models import HOURS_PER_YEAR


@dataclass
class Persistence:
    n: int
    mean_edge_apr: float | None
    min_edge_apr: float | None
    frac_positive: float | None
    hours_covered: float

    def to_dict(self):
        return self.__dict__.copy()


def edge_series(store, symbol: str, long_venue: str, long_kind: str, short_venue: str,
                lookback_h: float) -> list[tuple[float, float]]:
    """[(minute_ts, short_funding_1h - long_funding_1h)] -- long spot legs count as 0 funding."""
    since = time.time() - lookback_h * 3600
    with store.lock:
        rows = store.db.execute(
            "SELECT CAST(ts/60 AS INTEGER) AS m, venue, kind, AVG(funding_1h) FROM snapshots "
            "WHERE symbol=? AND ts>=? AND ((venue=? AND kind=?) OR (venue=? AND kind='perp')) "
            "GROUP BY m, venue, kind", (symbol, since, long_venue, long_kind, short_venue)).fetchall()
    by_min: dict[int, dict] = {}
    for m, v, k, fr in rows:
        by_min.setdefault(m, {})[(v, k)] = fr
    out = []
    for m, d in sorted(by_min.items()):
        s = d.get((short_venue, "perp"))
        if long_kind == "spot":
            if s is not None and (long_venue, "spot") in d:
                out.append((m * 60.0, s))
        else:
            lf = d.get((long_venue, "perp"))
            if s is not None and lf is not None:
                out.append((m * 60.0, s - lf))
    return out


def persistence(store, symbol, long_venue, long_kind, short_venue, lookback_h) -> Persistence:
    s = edge_series(store, symbol, long_venue, long_kind, short_venue, lookback_h)
    if not s:
        return Persistence(0, None, None, None, 0.0)
    e = [x for _, x in s]
    return Persistence(
        n=len(e),
        mean_edge_apr=sum(e) / len(e) * HOURS_PER_YEAR,
        min_edge_apr=min(e) * HOURS_PER_YEAR,
        frac_positive=sum(1 for x in e if x > 0) / len(e),
        hours_covered=(s[-1][0] - s[0][0]) / 3600.0,
    )
