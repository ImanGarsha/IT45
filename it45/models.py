"""Normalised market-data records shared by every venue adapter.

Units are fixed here once so no strategy has to know venue conventions:
- prices in USD
- funding_1h: FRACTION per hour, positive => longs pay shorts
  (Reya publishes PERCENT per hour; its adapter divides by 100 -- see docs/01_venue_apis.md)
- fees: FRACTION of notional (0.00045 = 4.5 bp)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time

HOURS_PER_YEAR = 24 * 365


@dataclass
class Quote:
    venue: str              # "hyperliquid" | "arcus" | "bulk" | "reya" | "meridian"
    symbol: str             # normalised base asset, e.g. "BTC", "SPY"
    raw_symbol: str         # venue's own ticker, e.g. "BTCRUSDPERP"
    kind: str = "perp"      # "perp" | "spot"
    category: str = "crypto"  # crypto | equity | index | commodity
    mid: float | None = None
    mark: float | None = None
    oracle: float | None = None
    bid: float | None = None
    ask: float | None = None
    funding_1h: float | None = None
    next_funding_1h: float | None = None
    volume_24h_usd: float | None = None
    open_interest_usd: float | None = None
    maker_fee: float | None = None
    taker_fee: float | None = None
    max_leverage: float | None = None
    status: str = "trading"   # trading | suspended | offline
    ts: float = field(default_factory=time.time)

    @property
    def px(self) -> float | None:
        """Best available price: mid, else mark, else oracle."""
        return self.mid or self.mark or self.oracle

    @property
    def funding_apr(self) -> float | None:
        return None if self.funding_1h is None else self.funding_1h * HOURS_PER_YEAR

    def to_dict(self) -> dict:
        d = asdict(self)
        d["px"] = self.px
        d["funding_apr"] = self.funding_apr
        return d


def f(x) -> float | None:
    """Parse a venue number (str/float/None/'') to float or None."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
