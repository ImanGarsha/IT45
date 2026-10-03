"""Hyperliquid -- public /info endpoint (POST). Perps from the main dex + spot.

funding: `funding` in metaAndAssetCtxs is the current hourly rate as a fraction.
fees: base tier (perps 1.5/4.5 bp, spot 4/7 bp) -- editable in Settings.
"""
from __future__ import annotations

import time

from ..models import Quote, f
from .base import Venue, norm_symbol

SPOT_MAKER, SPOT_TAKER = 0.0004, 0.0007
# /info weight: each of these calls costs 20 of the 1200/min per-IP budget. Perps refresh
# every poll; spot (330 pairs, only needed for carry) every SPOT_EVERY_S -- a 429 was seen
# polling both every 5 s from a shared IP.
SPOT_EVERY_S = 30


class Hyperliquid(Venue):
    name = "hyperliquid"
    label = "Hyperliquid"
    url = "https://app.hyperliquid.xyz"
    maker_fee = 0.00015
    taker_fee = 0.00045

    @property
    def api(self) -> str:
        return ("https://api.hyperliquid-testnet.xyz" if self.testnet
                else "https://api.hyperliquid.xyz") + "/info"

    async def fetch(self) -> list[Quote]:
        meta, ctxs = await self.post(self.api, {"type": "metaAndAssetCtxs"})
        out: list[Quote] = []
        for u, c in zip(meta["universe"], ctxs):
            if u.get("isDelisted"):
                continue
            imp = c.get("impactPxs") or [None, None]
            mark = f(c.get("markPx"))
            out.append(Quote(
                venue=self.name, symbol=norm_symbol(u["name"]), raw_symbol=u["name"],
                mid=f(c.get("midPx")), mark=mark, oracle=f(c.get("oraclePx")),
                bid=f(imp[0]), ask=f(imp[1]),
                funding_1h=f(c.get("funding")),
                volume_24h_usd=f(c.get("dayNtlVlm")),
                open_interest_usd=(f(c.get("openInterest")) or 0) * (mark or 0),
                maker_fee=self.maker_fee, taker_fee=self.taker_fee,
                max_leverage=f(u.get("maxLeverage")),
            ))
        if time.time() - getattr(self, "_spot_ts", 0) >= SPOT_EVERY_S:
            try:
                self._spot_cache = await self._spot()
                self._spot_ts = time.time()
            except Exception:
                if not getattr(self, "_spot_cache", None):
                    raise
        out.extend(getattr(self, "_spot_cache", []))
        return out

    async def _spot(self) -> list[Quote]:
        meta, ctxs = await self.post(self.api, {"type": "spotMetaAndAssetCtxs"})
        tokens = {t["index"]: t["name"] for t in meta["tokens"]}
        by_coin = {c["coin"]: c for c in ctxs}
        out = []
        for u in meta["universe"]:
            base, quote = tokens.get(u["tokens"][0]), tokens.get(u["tokens"][1])
            if quote != "USDC" or base is None:
                continue  # v1: USDC-quoted spot only
            c = by_coin.get(u["name"])
            if not c:
                continue
            out.append(Quote(
                venue=self.name, symbol=norm_symbol(base), raw_symbol=u["name"], kind="spot",
                mid=f(c.get("midPx")), mark=f(c.get("markPx")),
                volume_24h_usd=f(c.get("dayNtlVlm")),
                maker_fee=SPOT_MAKER, taker_fee=SPOT_TAKER,
            ))
        return out
