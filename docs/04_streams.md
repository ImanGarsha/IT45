# 04 — Live order-book streams

The REST feed (every 5 s) drives the screens and funding history. For the markets the bot is
actually working on, IT45 also opens each venue's public websocket for **top of book** and
**trade prints** (`it45/streams.py`). Subscriptions follow the engine every cycle:
legs of open pairs, resting paper orders, and both legs of each running strategy's top 4
candidates. Everything else stays on REST. Venue health shows what is subscribed and fresh.

## What it changes
- Paper books and executor prices use the live top of book when it is under 5 s old.
- **Paper maker fills now follow a queue model** instead of waiting for the market to move
  through the price between two polls:
  - joining the best bid puts the order behind the size already displayed there;
  - taker SELL prints at our bid consume that queue first, then fill us (maker fee);
  - a taker sell printed below our bid fills us at once (the market went through us);
  - taker BUY prints never fill a resting bid. Sells mirror all of this.
  Not modelled: queue jumps from cancellations ahead of us (pessimistic), hidden liquidity,
  partial fills (an order fills whole once its queue clears; IT45 orders are small).

First live measurement (2026-10-03, 12 minutes, paper): 2 of 4 leg fills were maker at $0 fee,
including a ZEC pair opened maker on both Bulk and Arcus. Before streaming, practically every
paper fill fell back to taker. Small sample; the fill split is now visible in Trade history.

## Formats (verified live 2026-10-03)
| Venue | URL | Book | Trades | Notes |
|---|---|---|---|---|
| Hyperliquid | wss://api.hyperliquid.xyz/ws | `bbo` | `trades` (side B = taker buy) | snapshots |
| Arcus | wss://api.arcus.xyz/v1/ws | `bbo` (snapshot in subscribe ack) | `trades` (side BUY/SELL = taker) | timestamps in µs |
| Bulk | wss://mainnet-ws1.bulk.trade | `l2Snapshot` nlevels 1, ~5/s | `trades` (side true = taker buy) | server pings every 30 s; book ts in ns |
| Reya | wss://ws.reya.xyz/ | `/v2/market/{SYM}/depth` snapshot + absolute deltas | `/perpExecutions` (side B = taker buy) | IT45 keeps the merged book |
| Meridian | wss://ws.meridian.xyz/v1/stream | `Ticker` (~1/s, bid/ask/amounts) | `TradeFill` (sd 0 = taker buy) | subscribes by ticker, e.g. BTCUSD |
Reya executions and Meridian TradeFill frames were not seen live during the probe (quiet BTC
markets); their parsers follow the docs and are unit-tested on documented shapes.

## Console noise on Windows (fixed 2026-10-06)
On the PI's Windows / Python 3.13 machine a websocket that failed while opening made asyncio print
`AttributeError: 'ClientConnection' object has no attribute 'recv_messages'` from inside the
websockets library (its `connection_lost()` runs before `connection_made()` on a failed TLS
handshake; still present in websockets 17.2). Harmless: the attempt had already failed and the
stream retries with backoff. `quiet_websocket_handshake_noise()` drops that one traceback and
writes one line to the event log at most every 10 minutes; the real reason stays visible under
Venue health, Live book.
