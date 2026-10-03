# IT45 — multi-venue automated trader

Successor to IT4-2. Trades funding and basis spreads across **Hyperliquid, Arcus, Bulk,
Reya and Meridian** through their APIs, from one dashboard with a left-hand menu.

The lesson carried over from IT4-2: fees, not forecasts, were the binding constraint. So IT45
trades spreads (funding differences between venues, Hyperliquid spot-perp carry) with
maker-first execution, rather than directional predictions.

**Current build: M2.** Live prices and funding from all five venues, and an automatic
trader for both strategies in **paper** mode on every venue (simulated fills on live prices)
and **testnet** mode on Hyperliquid. Every order passes a fail-closed guard; four brakes and a
kill switch run independently of the strategies. No mainnet yet (M3).

Strategies ship **stopped**. Start them under Strategies once a few hours of funding history
have been stored: they only open spreads that have persisted (default: 6 h average above
15 %/yr, positive 80 % of the time).

## Run it (Windows, PowerShell)

```powershell
cd "D:\PROJECTS\INTELLIGENT TRADER\IT45"
.\run.ps1
```

First run creates `.venv` and installs; then the dashboard opens at http://127.0.0.1:8045.
`.\run.ps1 --no-browser` or `--port 9000` also work. Stop with Ctrl+C.

If PowerShell refuses to run scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Menu

| Section | Pages |
|---|---|
| Overview | Summary, Venue health |
| Venues | one page per exchange: feed state, fees, every market |
| Markets | Funding matrix (+ history chart), Price matrix, Hyperliquid spot |
| Opportunities | Cross-venue funding, Spot-perp carry |
| Strategies | Funding arb, Spot carry: start/stop, live candidates, rules |
| Orders & positions | Positions (pairs, legs, accounts), Open orders, Trade history |
| Risk | Limits (venue modes, caps, leverage, execution), Brakes, Kill switch |
| Logs | Events |
| Settings | General, Fees, Venues, API keys |

## Milestones
- **M1**: read-only dashboard, all five venues, opportunity screen, SQLite history.
- **M2 (this build)**: paper trading on all venues, Hyperliquid testnet connector (agent key in
  `.env`), both strategies with persistence-gated entry, maker-first executor with lone-leg
  repair, guard, four brakes, kill switch. docs/02 (paper model), docs/03 (keys).
- **M2.x**: real connectors for Bulk and Meridian (both have testnets), then Arcus and Reya.
- **M3**: mainnet arming per venue, Telegram / email / Windows alerts, VPS packaging (Docker).

## Layout

```
it45/            Python package (FastAPI backend)
  venues/        one adapter per venue -> normalised Quote (docs/01 has the units)
  opportunities.py  pure functions: cross-venue arb, spot carry, matrix
  trading/       engine, brokers (paper, hl_testnet), guard, executor, strategies, risk
  feed.py        poller + venue health;  store.py  SQLite + settings
  api.py         /api/* JSON + serves the built dashboard from it45/web
  web/           built dashboard (committed, so Node is not needed to run)
frontend/        React + Vite source of the dashboard
tests/           pytest
docs/            00 spec, 01 venue APIs
data/            it45.db, settings.json (created at runtime, git-ignored)
```

## Develop

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend; npm install; npm run build    # rebuilds it45/web
npm run dev                                 # hot reload on :5173, proxies /api to :8045
```
