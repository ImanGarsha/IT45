"""FastAPI app: JSON API under /api, the built dashboard at /.

M1 is READ-ONLY by construction: no venue adapter holds a key or has an order method.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, opportunities as opp
from .feed import Feed
from .store import Settings, Store
from .trading import persistence as tpers
from .trading.engine import ConfigError, Engine

ROOT = Path(os.environ.get("IT45_HOME", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
WEB = Path(__file__).resolve().parent / "web"


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def create_app(start_feed: bool = True) -> FastAPI:
    load_dotenv(ROOT / ".env")
    settings = Settings(DATA / "settings.json")
    store = Store(DATA / "it45.db")
    feed = Feed(settings, store)
    engine = Engine(feed, settings, store)

    @asynccontextmanager
    async def lifespan(app):
        if start_feed:
            feed.start()
        yield
        await feed.stop()

    app = FastAPI(title="IT45", version=__version__, lifespan=lifespan)
    app.state.feed, app.state.settings, app.state.store = feed, settings, store
    app.state.engine = engine

    def _args(horizon, mode, min_vol):
        return (float(horizon or settings["horizon_hours"]),
                mode or settings["exec_mode"],
                float(settings["min_volume_usd"] if min_vol is None else min_vol))

    @app.get("/api/health")
    def health():
        vs = feed.venue_status()
        return {"version": __version__, "time": time.time(), "cycles": feed.cycles,
                "mode": "M2: paper + Hyperliquid testnet", "venues_ok": sum(v["ok"] for v in vs),
                "venues_enabled": sum(v["enabled"] for v in vs), **store.stats()}

    @app.get("/api/venues")
    def venues():
        return feed.venue_status()

    @app.get("/api/venues/{name}/markets")
    def venue_markets(name: str):
        if name not in feed.quotes:
            raise HTTPException(404, f"unknown venue {name!r}")
        return [q.to_dict() for q in feed.quotes[name]]

    @app.get("/api/matrix")
    def matrix(min_venues: int = 2):
        return opp.matrix(feed.all_quotes(include_stale=False), min_venues)

    @app.get("/api/opportunities")
    def opportunities(horizon: float | None = None, mode: str | None = None,
                      min_volume: float | None = None, limit: int = 200):
        h, m, mv = _args(horizon, mode, min_volume)
        rows = opp.cross_venue(feed.all_quotes(include_stale=False), h, m, mv)
        out = []
        for i, o in enumerate(rows[:limit]):
            d = o.to_dict()
            if i < 60:   # persistence costs one indexed query per row
                d["persistence"] = tpers.persistence(store, o.symbol, o.long_venue, o.long_leg,
                                                     o.short_venue, 6).to_dict()
            out.append(d)
        return {"horizon_hours": h, "mode": m, "min_volume_usd": mv, "rows": out}

    @app.get("/api/carry")
    def carry(horizon: float | None = None, mode: str | None = None,
              min_volume: float | None = None):
        h, m, mv = _args(horizon, mode, min_volume)
        rows = opp.spot_carry(feed.all_quotes(include_stale=False), h, m, mv)
        return {"horizon_hours": h, "mode": m, "min_volume_usd": mv,
                "rows": [o.to_dict() for o in rows]}

    @app.get("/api/history/{symbol}")
    def history(symbol: str, hours: float = 24, kind: str = "perp"):
        return store.history(symbol.upper(), kind, hours)

    @app.get("/api/logs")
    def logs(limit: int = 300):
        return store.events(limit)

    @app.get("/api/settings")
    def get_settings():
        return settings.data

    @app.put("/api/settings")
    def put_settings(patch: dict):
        if "exec_mode" in patch and patch["exec_mode"] not in ("maker", "taker"):
            raise HTTPException(400, "exec_mode must be maker or taker")
        if "poll_seconds" in patch and float(patch["poll_seconds"]) < 2:
            raise HTTPException(400, "poll_seconds must be >= 2 (Bulk allows 5 req/s per IP)")
        out = settings.update(patch)
        store.log("info", "settings", f"updated: {sorted(patch)}")
        return out

    # ---- trading (M2) ------------------------------------------------------
    def _bad(e):
        raise HTTPException(400, str(e))

    @app.get("/api/trading/status")
    async def trading_status():
        return await engine.status()

    @app.put("/api/trading/config")
    def trading_config(patch: dict):
        try:
            return engine.update_cfg(patch)
        except ConfigError as e:
            _bad(e)

    @app.get("/api/trading/accounts")
    async def trading_accounts():
        return await engine.accounts()

    @app.get("/api/trading/positions")
    async def trading_positions():
        return [p.to_dict() for p in await engine.all_positions()]

    @app.get("/api/trading/pairs")
    def trading_pairs(include_closed: bool = True, limit: int = 200):
        if include_closed:
            return engine.db.q("SELECT * FROM pairs ORDER BY opened_ts DESC LIMIT ?", (limit,))
        return engine.db.open_pairs()

    @app.get("/api/trading/orders")
    def trading_orders(status: str | None = None, limit: int = 300):
        if status:
            return engine.db.q("SELECT * FROM orders WHERE status=? ORDER BY ts DESC LIMIT ?", (status, limit))
        return engine.db.q("SELECT * FROM orders ORDER BY ts DESC LIMIT ?", (limit,))

    @app.get("/api/trading/fills")
    def trading_fills(limit: int = 500):
        return engine.db.q("SELECT * FROM fills ORDER BY ts DESC LIMIT ?", (limit,))

    @app.get("/api/trading/funding")
    def trading_funding(limit: int = 500):
        return engine.db.q("SELECT * FROM funding_payments ORDER BY ts DESC LIMIT ?", (limit,))

    @app.get("/api/trading/audit")
    def trading_audit(limit: int = 300):
        return engine.db.q("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,))

    @app.get("/api/trading/candidates/{strategy}")
    def trading_candidates(strategy: str):
        s = engine.strategies.get(strategy)
        if not s:
            raise HTTPException(404, "unknown strategy")
        return {"note": s.last_note, "last_eval": s.last_eval, "rows": s.candidates()}

    @app.post("/api/trading/kill")
    async def trading_kill():
        return await engine.kill("kill switch pressed in the dashboard")

    @app.post("/api/trading/release")
    def trading_release():
        engine.release_kill()
        return {"ok": True}

    @app.post("/api/trading/pairs/{pair_id}/close")
    async def trading_close_pair(pair_id: str):
        try:
            await engine.close_pair(pair_id)
        except ConfigError as e:
            _bad(e)
        return {"ok": True}

    @app.post("/api/trading/venues/{venue}/resume")
    def trading_resume(venue: str):
        engine.resume_venue(venue)
        return {"ok": True}

    @app.post("/api/trading/venues/{venue}/reset-paper")
    def trading_reset(venue: str):
        try:
            engine.reset_paper(venue)
        except ConfigError as e:
            _bad(e)
        return {"ok": True}

    # ---- dashboard (built React app) --------------------------------------
    if (WEB / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": f"no API route /{path} -- restart IT45 if you "
                                     "just updated it"}, status_code=404)
            f = WEB / path
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    return app
