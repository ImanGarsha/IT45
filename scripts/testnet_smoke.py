r"""Live testnet smoke test for one venue connector, using the keys in .env.

    .\.venv\Scripts\python.exe scripts\testnet_smoke.py bulk        (or hyperliquid / meridian)

Steps (each printed): read account -> post-only buy 10% under the market (must rest) ->
cancel it -> IOC buy of the smallest size the venue allows -> reduce-only IOC sell -> position flat.
Touches TESTNET only. The open/close round trip costs a few cents of play money.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from it45.api import load_dotenv  # noqa: E402
from it45.store import Store  # noqa: E402
from it45.trading.db import TradeDB  # noqa: E402


def make(venue, db):
    if venue == "bulk":
        from it45.trading.bulk_testnet import BulkTestnet
        return BulkTestnet(db, os.environ, lambda: {"leverage": 2.0})
    if venue == "meridian":
        from it45.trading.meridian_testnet import MeridianTestnet
        return MeridianTestnet(db, os.environ)
    if venue == "hyperliquid":
        from it45.trading.hl_testnet import HyperliquidTestnet
        return HyperliquidTestnet(db, os.environ)
    raise SystemExit(f"unknown venue {venue}")


async def main(venue, symbol="BTC", usd=20.0):
    load_dotenv(ROOT / ".env")
    db = TradeDB(Store(ROOT / "data" / "testnet_smoke.db"))
    br = make(venue, db)
    print("account :", await br.account())
    b = await br.book(symbol, "perp")
    print("book    :", b)
    size = usd / b.mid
    a = await br.place(symbol=symbol, kind="perp", side="buy", size=size, price=b.bid * 0.9, post_only=True)
    print("rest    :", a)
    await asyncio.sleep(2)
    print("cancel  :", await br.cancel(a.order_id))
    a = await br.place(symbol=symbol, kind="perp", side="buy", size=size, price=b.ask * 1.005, post_only=False)
    print("open    :", a)
    await asyncio.sleep(3)
    print("status  :", await br.status(a.order_id))
    pos = [p for p in await br.positions() if p.symbol == symbol]
    print("position:", pos)
    if pos:
        b = await br.book(symbol, "perp")
        a = await br.place(symbol=symbol, kind="perp", side="sell", size=abs(pos[0].size), price=b.bid * 0.995,
                           post_only=False, reduce_only=True)
        print("close   :", a)
        await asyncio.sleep(3)
        print("status  :", await br.status(a.order_id))
    print("after   :", [p for p in await br.positions() if p.symbol == symbol])
    print("account :", await br.account())


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:2] or ["bulk"]))
