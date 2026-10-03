# IT45 notes for Claude

- Read `docs/00_SPEC.md` (PI's decisions) and `docs/01_venue_apis.md` (units, limits) first.
- Funding is normalised to FRACTION PER HOUR everywhere. Reya publishes percent per hour (÷100).
- Orders only go through `trading/executor.py` -> `Guard.check`. A new venue connector is a
  `Broker` subclass (see brokers.py / hl_testnet.py), exposes only order/cancel, reads keys
  from `.env`, and gets a testnet before it gets mainnet. No mainnet before M3 arming.
- Pair entries are persistence-gated (trading/persistence.py). Do not bypass it.
- Opportunity numbers are "if today's rate held" screens, not forecasts. Before any strategy
  trades on them, measure spread persistence from `data/it45.db` snapshots.
- After frontend edits run `npm run build` in `frontend/` — `it45/web` is committed.
- Never kill processes with a pattern that appears in the same shell command.
