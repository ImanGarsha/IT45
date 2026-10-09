# IT45 — multi-venue automated trader

Successor to IT4-2. Trades funding and basis spreads across **Hyperliquid, Arcus, Bulk,
Reya and Meridian** through their APIs, from one dashboard with a left-hand menu.

The lesson carried over from IT4-2: fees, not forecasts, were the binding constraint. So IT45
trades spreads (funding differences between venues, Hyperliquid spot-perp carry) with
maker-first execution, rather than directional predictions.

**Current build: M2.x.** Live prices and funding from all five venues, and an automatic
trader for both strategies in **paper** mode on every venue (simulated fills on live prices)
and **testnet** mode on Hyperliquid, Bulk and Meridian. Every order passes a fail-closed guard; four brakes and a
kill switch run independently of the strategies. No mainnet yet (M3).

Strategies ship **stopped**. Start them under Strategies once a few hours of funding history
have been stored: they only open spreads that have persisted (default: 6 h average above
15 %/yr, positive 80 % of the time).

## Run it (Windows, PowerShell)

```powershell
cd "D:\PROJECTS\INTELLIGENT TRADER\IT45"
.\run.ps1
```

First run creates `.venv` and installs; later starts skip the install unless the dependency list
(`pyproject.toml`) changed, so they take seconds. `.\run.ps1 --reinstall` forces it. The dashboard
opens at http://127.0.0.1:8045.
`.\run.ps1 --no-browser` or `--port 9000` also work. Stop with Ctrl+C.

If PowerShell refuses to run scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## User guide
The full manual (every page, every setting with its default, manual trading, brakes, keys,
troubleshooting, glossary) is `docs/IT45_User_Guide.pdf` (23 pages), also kept as the editable **IT45 User Guide** doc in
the INTELLIGENT TRADER project on claude.ai. The hover help below is its short form inside the app.

## Hover help
Rest the pointer on anything (menu item, column header, number, button, setting) for half a
second and a plain-English explanation appears. A table cell explains its column. The **?**
button in the top bar turns it off and on. The texts live in `frontend/src/help.ts`;
`scripts/help_audit.mjs` checks every page for elements without help (currently 0 of 1,958).

## Manual orders
**Orders & positions → New order** lets you trade yourself on any venue set to paper or testnet:
- **Single order**: market, limit (rests as maker unless it crosses) or post-only; size in USD or
  units; reduce-only. A live side panel shows the book, your position, equity, margin and fee.
  *Review order* shows exactly what will be sent; *Place* sends it.
- **Hedged pair**: long on one venue, short on another (or HL spot vs perp), same size, maker first.
  Recorded as a `manual` pair, so the imbalance and liquidation brakes watch it and the bot will
  not stack onto it.
- **Open orders → Cancel** removes a resting order; **Positions → Close** shuts one leg (refused
  for a leg owned by an open pair: close the pair instead).

Manual orders pass the same guard as the bot (kill switch, venue mode, price sanity, caps) and
are audited. No mainnet until M3.

## Menu

| Section | Pages |
|---|---|
| Overview | Summary, Venue health |
| Venues | one page per exchange: feed state, fees, every market |
| Markets | Funding matrix (+ history chart), Price matrix, Hyperliquid spot |
| Opportunities | Cross-venue funding, Spot-perp carry |
| Strategies | Funding arb, Spot carry: start/stop, live candidates, rules |
| Orders & positions | **New order** (manual ticket), Positions (pairs, legs, accounts, Close), Open orders (Cancel), Trade history |
| Risk | Limits (venue modes, caps, leverage, execution), Brakes, Kill switch |
| Logs | Events |
| Settings | General, Fees, Venues, API keys |

## Milestones
- **M1**: read-only dashboard, all five venues, opportunity screen, SQLite history.
- **M2**: paper trading on all venues, Hyperliquid testnet connector (agent key in
  `.env`), both strategies with persistence-gated entry, maker-first executor with lone-leg
  repair, guard, four brakes, kill switch. docs/02 (paper model), docs/03 (keys).
- **M2.x (this build)**: real testnet connectors for **Bulk** (verified live end to end) and
  **Meridian** (signing verified live), setup scripts for both, `scripts/testnet_smoke.py`.
- **Streams**: live websocket top of book + trade prints on all five venues for the markets in play; paper maker fills follow a queue model (docs/04).
- **Outages**: 10 s request limits, orders sent without a reply are reconciled with the venue, outage banners (docs/05).
- **Manual trading**: order ticket, hedged pairs, close and cancel from the dashboard.
- Next: Arcus and Reya connectors once a testnet is available; M3 mainnet arming.
- **M3**: mainnet arming per venue, Telegram / email / Windows alerts, VPS packaging (Docker).

## Layout

```
it45/            Python package (FastAPI backend)
  venues/        one adapter per venue -> normalised Quote (docs/01 has the units)
  opportunities.py  pure functions: cross-venue arb, spot carry, matrix
  trading/       engine, brokers (paper, hl/bulk/meridian testnet), guard, executor, strategies, risk
  streams.py     live websocket books/trades (5 venues)
  feed.py        poller + venue health;  store.py  SQLite + settings
  api.py         /api/* JSON + serves the built dashboard from it45/web
  web/           built dashboard (committed, so Node is not needed to run)
frontend/        React + Vite source of the dashboard
tests/           pytest
docs/            00 spec, 01 venue APIs, 02 paper model, 03 keys + testnet setup, 04 streams, 05 outages, user guide PDF
scripts/         testnet wallet/signer setup, testnet smoke test
data/            it45.db, settings.json (created at runtime, git-ignored)
```

## Develop

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend; npm install; npm run build    # rebuilds it45/web
npm run dev                                 # hot reload on :5173, proxies /api to :8045
```
