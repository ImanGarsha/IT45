"""Bulk -- public REST (docs.bulk.trade). Mainnet https://mainnet-api1.bulk.trade/api/v1,
testnet https://exchange-api.bulk.trade/api/v1.

GET /exchangeInfo lists markets; GET /ticker/{symbol} per market carries mark/oracle/
fundingRate (hourly fraction). Rate limit 5 req/s sustained per IP, so only TRADING
markets are polled. Base tier taker 3.5 bp / maker 0 (editable).
"""
from __future__ import annotations

import asyncio

from ..models import Quote, f
from .base import COMMODITY, Venue, norm_symbol


class Bulk(Venue):
    name = "bulk"
    label = "Bulk"
    url = "https://app.bulk.trade"
    maker_fee = 0.0
    taker_fee = 0.00035

    @property
    def api(self) -> str:
        return ("https://exchange-api.bulk.trade/api/v1" if self.testnet
                else "https://mainnet-api1.bulk.trade/api/v1")

    async def fetch(self) -> list[Quote]:
        info = await self.get(f"{self.api}/exchangeInfo")
        live = [m for m in info if m.get("status") == "TRADING"]
        tickers = await asyncio.gather(
            *(self.get(f"{self.api}/ticker/{m['symbol']}") for m in live),
            return_exceptions=True)
        out = []
        for m, t in zip(live, tickers):
            if isinstance(t, Exception):
                continue
            sym = norm_symbol(m["symbol"])
            mark = f(t.get("markPrice"))
            out.append(Quote(
                venue=self.name, symbol=sym, raw_symbol=m["symbol"],
                category="commodity" if sym in COMMODITY else "crypto",
                mid=f(t.get("lastPrice")), mark=mark, oracle=f(t.get("oraclePrice")),
                funding_1h=f(t.get("fundingRate")),
                volume_24h_usd=f(t.get("quoteVolume")),
                open_interest_usd=(f(t.get("openInterest")) or 0) * (mark or 0),
                maker_fee=self.maker_fee, taker_fee=self.taker_fee,
                max_leverage=f(m.get("maxLeverage")),
            ))
        return out
