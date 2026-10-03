"""Arcus -- public REST https://api.arcus.xyz (docs.arcus.xyz).

GET /v1/markets carries mark/oracle/fundingRate/nextFundingRate per market;
GET /v1/mids carries book mids. Funding is charged hourly; `fundingRate` is the
hourly fraction. Base taker 2.25 bp / maker 0 (editable).
"""
from __future__ import annotations

from ..models import Quote, f
from .base import Venue, norm_symbol

CATEGORY = {"CRYPTO": "crypto", "EQUITIES": "equity", "INDICES": "index",
            "COMMODITIES": "commodity"}


class Arcus(Venue):
    name = "arcus"
    label = "Arcus"
    url = "https://app.arcus.xyz"
    maker_fee = 0.0
    taker_fee = 0.000225
    api = "https://api.arcus.xyz"

    async def fetch(self) -> list[Quote]:
        markets = (await self.get(f"{self.api}/v1/markets"))["markets"]
        try:
            mids = (await self.get(f"{self.api}/v1/mids")).get("mids", {})
        except Exception:
            mids = {}
        out = []
        for m in markets:
            if m.get("type") != "PERPETUAL":
                continue
            raw = m["marketDisplayName"]
            mark = f(m.get("markPrice"))
            out.append(Quote(
                venue=self.name, symbol=norm_symbol(raw), raw_symbol=raw,
                category=CATEGORY.get(m.get("category", ""), "crypto"),
                mid=f(mids.get(raw)), mark=mark, oracle=f(m.get("oraclePrice")),
                funding_1h=f(m.get("fundingRate")),
                next_funding_1h=f(m.get("nextFundingRate")),
                volume_24h_usd=f(m.get("volume24hNotional")),
                open_interest_usd=(f(m.get("openInterest")) or 0) * (mark or 0),
                maker_fee=self.maker_fee, taker_fee=self.taker_fee,
                max_leverage=(1 / f(m["initialMarginFraction"])
                              if f(m.get("initialMarginFraction")) else None),
                status="trading" if m.get("status") == "ONLINE" else "offline",
            ))
        return out
