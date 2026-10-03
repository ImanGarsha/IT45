# IT45 notes for Claude

- Read `docs/00_SPEC.md` (PI's decisions) and `docs/01_venue_apis.md` (units, limits) first.
- Funding is normalised to FRACTION PER HOUR everywhere. Reya publishes percent per hour (÷100).
- M1 is read-only by design: no adapter may gain an order method without the M2 guard
  (trade-only keys from `.env`, per-venue Arm toggle, caps, kill switch, brakes).
- Opportunity numbers are "if today's rate held" screens, not forecasts. Before any strategy
  trades on them, measure spread persistence from `data/it45.db` snapshots.
- After frontend edits run `npm run build` in `frontend/` — `it45/web` is committed.
- Never kill processes with a pattern that appears in the same shell command.
