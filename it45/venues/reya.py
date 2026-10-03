"""Reya -- public REST v2 https://api.reya.xyz/v2 (docs.reya.xyz).

UNIT TRAP: `fundingRate` in /perpMarkets/summary is an HOURLY rate already in
PERCENT (docs: fundingRate = dailyRate / 24 * 100). We divide by 100 to get the
hourly fraction every other venue uses. Taker 3 bp / maker 0 at tier 0 (/feeTiers).
"""
from __future__ import annotations

import re

from ..models import Quote, f
from .base import Venue, norm_symbol


def reya_base(sym: str) -> str:
    # BTCRUSDPERP -> BTC ; HYPERUSDPERP -> HYPER -> HYPE (alias)
    return norm_symbol(re.sub(r"RUSDPERP$|USDPERP$", "", sym))


class Reya(Venue):
    name = "reya"
    label = "Reya"
    url = "https://app.reya.xyz"
    maker_fee = 0.0
    taker_fee = 0.0003

    @property
    def api(self) -> str:
        # Reya's public docs (API 3.6.2) list no testnet host; resolved in M2 when
        # order entry needs it. Market data is read from mainnet either way.
        return "https://api.reya.xyz/v2"

    async def fetch(self) -> list[Quote]:
        summ = await self.get(f"{self.api}/perpMarkets/summary")
        try:
            defs = {d["symbol"]: d for d in await self.get(f"{self.api}/perpMarketDefinitions")}
        except Exception:
            defs = {}
        out = []
        for s in summ:
            raw = s["symbol"]
            mark = f(s.get("markPrice"))
            fr = f(s.get("fundingRate"))
            out.append(Quote(
                venue=self.name, symbol=reya_base(raw), raw_symbol=raw,
                mid=f(s.get("throttledMidPrice")), mark=mark, oracle=f(s.get("oraclePrice")),
                funding_1h=None if fr is None else fr / 100.0,
                volume_24h_usd=f(s.get("volume24h")),
                open_interest_usd=(f(s.get("oiQty")) or 0) * (mark or 0),
                maker_fee=self.maker_fee, taker_fee=self.taker_fee,
                max_leverage=f(defs.get(raw, {}).get("maxLeverage")),
            ))
        return out
