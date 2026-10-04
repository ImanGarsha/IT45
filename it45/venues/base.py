from __future__ import annotations

import abc
import re

import httpx

from ..models import Quote

# venue-specific aliases for the same underlying
ALIASES = {
    "UBTC": "BTC", "UETH": "ETH", "USOL": "SOL",  # Hyperliquid spot wrapped tokens
    "KBONK": "kBONK",
}

EQUITY_INDEX = {"SPY", "QQQ", "VT", "SGOV"}
COMMODITY = {"XAU", "XAG", "GLD", "SLV", "USO", "CPER", "PAXG"}


def norm_symbol(raw: str) -> str:
    s = raw.upper()
    s = re.sub(r"(-USD|/USDC|USD)$", "", s)
    return ALIASES.get(s, s)


class Venue(abc.ABC):
    name: str = ""
    label: str = ""
    url: str = ""
    # defaults used when the venue does not publish fees per market (base tier)
    maker_fee: float = 0.0
    taker_fee: float = 0.0

    def __init__(self, client: httpx.AsyncClient, testnet: bool = False):
        self.client = client
        self.testnet = testnet

    @abc.abstractmethod
    async def fetch(self) -> list[Quote]:
        """Return every market's current quote. Must raise on transport errors."""

    async def get(self, url: str, **kw):
        r = await self.client.get(url, **kw)
        r.raise_for_status()
        return r.json()

    async def post(self, url: str, json):
        r = await self.client.post(url, json=json)
        r.raise_for_status()
        return r.json()

    def info(self) -> dict:
        return {"name": self.name, "label": self.label, "url": self.url,
                "maker_fee": self.maker_fee, "taker_fee": self.taker_fee,
                "testnet": self.testnet}
