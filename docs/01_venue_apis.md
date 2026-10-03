# 01 — Venue APIs: what IT45 reads, and the unit of every number

Probed live on 2026-10-03. Market data needs no key on any venue. Every adapter turns the
venue's numbers into one convention, defined in `it45/models.py`:

- **funding_1h** = fraction per hour; positive means longs pay shorts
- **fees** = fraction of notional (0.00045 = 4.5 bp)

All five venues settle funding **hourly**, so per-hour rates can be compared directly.

| Venue | Base URL (mainnet) | Calls per poll | Funding field → conversion | Base fees maker / taker |
|---|---|---|---|---|
| Hyperliquid | `POST https://api.hyperliquid.xyz/info` | `metaAndAssetCtxs` every poll; `spotMetaAndAssetCtxs` every 30 s | `funding`, already hourly fraction | perp 1.5 / 4.5 bp, spot 4 / 7 bp |
| Arcus | `https://api.arcus.xyz` | `GET /v1/markets`, `GET /v1/mids` | `fundingRate`, hourly fraction (`nextFundingRate` = predicted) | 0 / 2.25 bp |
| Bulk | `https://mainnet-api1.bulk.trade/api/v1` | `GET /exchangeInfo`, then `GET /ticker/{sym}` for each TRADING market | `fundingRate`, hourly fraction | 0 / 3.5 bp (tier 1) |
| Reya | `https://api.reya.xyz/v2` | `GET /perpMarkets/summary`, `GET /perpMarketDefinitions` | **`fundingRate` is PERCENT per hour → ÷ 100** | 0 / 3 bp (`GET /feeTiers` tier 0) |
| Meridian | `https://api.meridian.xyz` | `GET /v1/product`, `GET /v1/product/market-price?productIds=…` | `fundingRate1h`, hourly fraction | per product (now 0 / 0.5 bp) |

## Unit trap: Reya
Reya's docs say: "The `fundingRate` value in market data is an **hourly** rate, **already
expressed as a percentage** (`fundingRate = dailyRate ÷ 24 × 100`)". A raw 0.00125 is
0.00125 % per hour = 10.95 %/yr, which is the 0.01 %/8h base rate every other venue shows.
Read without the ÷100 it would be 1,095 %/yr. `tests/test_venue_units.py` pins this.

## Base rates (why so many cells read 10.95 %)
Crypto perps on Hyperliquid, Arcus, Bulk and Reya default to 0.01 % per 8 h = 0.00125 %/h
= 10.95 %/yr when the premium is small. Arcus RWA perps (stocks, indices, metals) use
SOFR + 0.5 %/yr instead (≈ 4.4 %/yr). Meridian's baseline is 10 %/yr. The funding matrix
therefore shades each cell **relative to the asset's median across venues**, not by its
absolute value, so the spreads stand out.

## Symbol mapping
`norm_symbol()` strips `-USD`, `USD`, `/USDC`. Aliases: Reya `HYPER` → `HYPE`; Hyperliquid
spot `UBTC`/`UETH`/`USOL` → `BTC`/`ETH`/`SOL`. Meridian uses `baseTokenName`
(`XAUXAUUSD` → `XAU`). Two venues' contracts priced more than 5 % apart are treated as
different contracts (e.g. a 1000× unit listing) and never paired; 0.5–5 % gets a flag.

Arcus `GLD` (the ETF) and Meridian `XAU` (gold spot) are different underlyings and are
deliberately **not** matched.

## Rate limits that shaped the poller
- Hyperliquid: 1200 weight/min per IP; each `/info` call above weighs 20. A 429 was seen
  polling both calls every 5 s from a shared IP → spot moved to 30 s.
- Bulk: 5 HTTP requests/s sustained per IP (burst 150). One ticker call per trading market
  (8 today) per poll → `poll_seconds` has a floor of 2.

## Testnets (for M2 order entry)
| Venue | Testnet market data | Status |
|---|---|---|
| Hyperliquid | `https://api.hyperliquid-testnet.xyz/info` | exists |
| Bulk | `https://exchange-api.bulk.trade/api/v1` | exists (faucet) |
| Meridian | `https://api.meridiantest.net` | exists (faucet) |
| Arcus | not found in public docs | paper fills on the live book |
| Reya | not listed in API 3.6.2 docs | paper fills until found |

## Not modelled yet
- Meridian mPerps (XAU, XAG, SPY, QQQ) also charge **position fees** during scheduled
  mark-price gaps (`GET /v1/position-fee/projected-rate`). Flagged in the opportunity table.
- Arcus RWA perps run off-hours funding and price bands. Flagged.
- Bulk `lastPrice` is used as its mid (the ticker has no book mid), so Bulk price gaps are
  approximate.

## Sources
- https://docs.arcus.xyz/llms.txt
- https://docs.reya.xyz/developers/pricing-and-funding/funding.md
- https://docs.bulk.trade/api-reference/introduction.md, /bulk-exchange/fees.md, /api-reference/rate-limits.md
- https://docs.meridian.xyz/protocol-reference/api-hosts.md, /developer-guides/trading-api/products.md, /trading/perpetual-futures/funding-rates.md
