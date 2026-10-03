"""Meridian -- public REST https://api.meridian.xyz (testnet api.meridiantest.net).

GET /v1/product: fees (per product), fundingRate1h (hourly fraction), status.
GET /v1/product/market-price?productIds=a,b: best bid/ask + oracle.
mPerps (XAU/XAG/SPY/QQQ) may also accrue POSITION FEES during mark-price gaps --
not modelled in M1 (flagged in the opportunity table).
"""
from __future__ import annotations

from ..models import Quote, f
from .base import COMMODITY, EQUITY_INDEX, Venue, norm_symbol


class Meridian(Venue):
    name = "meridian"
    label = "Meridian"
    url = "https://app.meridian.xyz"
    maker_fee = 0.0
    taker_fee = 0.00005

    @property
    def api(self) -> str:
        return "https://api.meridiantest.net" if self.testnet else "https://api.meridian.xyz"

    async def fetch(self) -> list[Quote]:
        prods, cursor = [], None
        for _ in range(10):  # cursor pagination
            params = {"limit": 100, **({"cursor": cursor} if cursor else {})}
            page = await self.get(f"{self.api}/v1/product", params=params)
            prods += page.get("data", [])
            if not page.get("hasNext"):
                break
            cursor = page.get("nextCursor")
        prods = [p for p in prods if p.get("status") == "ACTIVE"]
        prices = {}
        if prods:
            ids = ",".join(p["id"] for p in prods)
            mp = await self.get(f"{self.api}/v1/product/market-price", params={"productIds": ids})
            prices = {x["productId"]: x for x in mp.get("data", [])}
        out = []
        for p in prods:
            sym = norm_symbol(p.get("baseTokenName") or p["ticker"])
            px = prices.get(p["id"], {})
            bid, ask = f(px.get("bestBidPrice")), f(px.get("bestAskPrice"))
            oracle = f(px.get("oraclePrice"))
            mid = (bid + ask) / 2 if bid and ask else None
            cat = ("index" if sym in EQUITY_INDEX else
                   "commodity" if sym in COMMODITY else "crypto")
            out.append(Quote(
                venue=self.name, symbol=sym, raw_symbol=p.get("displayTicker", p["ticker"]),
                category=cat, mid=mid, oracle=oracle, bid=bid, ask=ask,
                funding_1h=f(p.get("fundingRate1h")),
                volume_24h_usd=(f(p.get("volume24h")) or 0) * (mid or oracle or 0),
                open_interest_usd=(f(p.get("openInterest")) or 0) * (mid or oracle or 0) / 2,
                maker_fee=f(p.get("makerFee")), taker_fee=f(p.get("takerFee")),
                max_leverage=f(p.get("maxLeverage")),
            ))
        return out
