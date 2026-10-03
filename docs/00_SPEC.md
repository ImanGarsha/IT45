# IT45 — multi-venue automated trader (spec v0, PI questionnaire 2026-10-03)

Successor in spirit to IT4-2. Lesson carried over: execution cost is the binding constraint,
so v1 trades SPREADS (funding/basis) with maker-first execution, not directional forecasts.

## Venues (PI confirmed eligibility on all five; accounts exist on all five)
| venue | signing | testnet | v1 mode |
|---|---|---|---|
| Hyperliquid | agent wallet (EVM) | yes | testnet auto -> mainnet armed |
| Reya | EIP-712 signer, chainId 1729 | yes | testnet auto -> mainnet armed |
| Arcus | Ed25519 API key (trade-only) | check | paper-sim on live book -> mainnet armed |
| Bulk | Ed25519 agent key | check | paper-sim on live book -> mainnet armed |
| Meridian | EVM linked signer, chainId 4663 | check | paper-sim on live book -> mainnet armed |
Keys: trade-only agent/sub keys in gitignored `.env`; NO withdrawal permission; main wallet keys never in IT45.

## Strategies (v1)
1. Cross-venue funding arb — delta-neutral long cheap-funding venue / short rich-funding venue.
2. Hyperliquid spot-perp carry — long HL spot + short HL perp.
Entry/exit thresholds tunable in GUI (ship conservative defaults). Exits (all four):
spread < exit threshold (hysteresis), max holding time, rotate to better pair net of switching
fees, close on leg price divergence > X%.

## Markets
Auto-discovered: every crypto perp listed on 2+ venues (BTC/ETH/SOL first), stock/commodity
perps (Arcus, Meridian), Hyperliquid spot.

## Execution
Maker-first: post-only limit, reprice N times, then taker fallback. Hedge-imbalance guard unwinds
a lone leg after N seconds.

## Risk
Capital caps per venue: tiny defaults, set in Settings. Leverage configurable up to 10x per leg.
Brakes (all on): daily loss limit -> flatten + disarm; hedge-imbalance guard; venue stale-data /
API-health stop; margin-ratio / liquidation-distance reduce. Global kill switch. Mainnet needs an
explicit Arm toggle per venue.

## Dashboard (left vertical menu, collapsible, each section has submenus; dark/light toggle)
- Overview — PnL, exposure, venue health
- Venues — one submenu per exchange: balances, positions, orders, API status
- Markets — funding / price matrix across venues
- Opportunities — ranked arb candidates, net of fees
- Strategies — config, start/stop, arm
- Orders & Positions — live blotter, trade + funding history
- Risk — caps, brakes, kill switch
- Logs — event log
- Settings — keys/venue setup, thresholds, alerts

## Alerts
In-dashboard toasts, Telegram bot, email, Windows desktop notification.

## Stack / ops
FastAPI (async, one websocket client per venue) + React/Vite. SQLite for trades, funding,
PnL. Runs on the PI's laptop, Docker-ready for a VPS later. Reuse from IT4-2 only: Hyperliquid
connector, fail-closed order guard, kill switch. Repo: new private GitHub repo `IT45`, Claude pushes.

## Milestones
- M1 — READ-ONLY dashboard: all 5 venues streaming prices + funding, opportunity table. No orders.
- M2 — order connectors, testnet/paper auto for both strategies, brakes.
- M3 — mainnet arming, alerts, VPS packaging.
