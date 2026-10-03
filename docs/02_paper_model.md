# 02 — How paper trading simulates a venue

Paper mode trades against each venue's **live** public prices with play money. It is the
default for every venue, and the only mode for Arcus, Bulk, Reya and Meridian until their
real connectors land.

## Account
- Each venue gets its own paper account (default $1,000; Risk → Limits). Reset per venue
  from Orders & positions → Positions once it holds nothing.
- Margin: perp legs need notional ÷ leverage; spot legs need full notional. An order that
  would exceed free collateral is rejected.
- Liquidation estimate per perp leg (isolated style): entry × (1 ∓ (1/leverage − mmr)), with
  mmr = 0.5 ÷ the market's max leverage. Conservative: real cross margin would use the whole
  venue balance.

## Prices
The venue's bid/ask when it publishes one (Meridian; Hyperliquid impact prices), otherwise
mid ± 2 bp.

## Fills
| Order | Rule |
|---|---|
| Post-only buy at P | Rejected if P ≥ ask. Rests; fills **only once the ask trades below P**, at P, maker fee. |
| Post-only sell at P | Mirror image. |
| IOC (taker) buy, limit L | Fills the full size at the ask if ask ≤ L, taker fee; otherwise nothing. |

There is no order-book depth or queue model. That is acceptable only because caps keep
orders far below each venue's volume; it flatters large orders. With quotes refreshed every
5 s, resting post-only orders fill less often than they would live (the market must move
through the price between two polls), so paper maker fill rates are pessimistic and the
executor's taker fallback is used more than it would be.

## Funding
Accrued continuously on every perp leg each cycle: payment = −size × price × funding/h × Δt.
Longs pay positive rates. Booked to `funding_payments` once a minute. Spot legs pay none.

## Fees
The venue's base maker/taker rates, or your overrides under Settings → Fees.

## What this does not model
Depth, queue position, partial fills, venue outages mid-order, funding settlement timing
(accrual is continuous; venues settle hourly), Meridian position fees, Arcus off-hours bands.

## Testnet versus paper
A venue in **testnet** mode sends real signed orders to that venue's test exchange. The
strategies still choose pairs from **mainnet** funding (the feed), because testnet funding and
prices are play-market values. Testnet therefore proves the plumbing (signing, order types,
fills, cancels, positions, brakes, kill switch) and not the profit: P&L that matters comes from
paper on live mainnet prices until M3.
