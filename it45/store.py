"""SQLite persistence: funding/price snapshots + event log. One file: data/it45.db."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts REAL NOT NULL, venue TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL,
    px REAL, funding_1h REAL, volume_24h_usd REAL
);
CREATE INDEX IF NOT EXISTS ix_snap ON snapshots(symbol, venue, ts);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL, level TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def save_snapshot(self, quotes) -> int:
        rows = [(q.ts, q.venue, q.symbol, q.kind, q.px, q.funding_1h, q.volume_24h_usd)
                for q in quotes]
        with self.lock:
            self.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", rows)
            self.db.commit()
        return len(rows)

    def history(self, symbol: str, kind: str = "perp", hours: float = 24) -> list[dict]:
        since = time.time() - hours * 3600
        with self.lock:
            cur = self.db.execute(
                "SELECT ts, venue, px, funding_1h FROM snapshots "
                "WHERE symbol=? AND kind=? AND ts>=? ORDER BY ts", (symbol, kind, since))
            return [{"ts": t, "venue": v, "px": p, "funding_1h": fr} for t, v, p, fr in cur]

    def log(self, level: str, source: str, message: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO events VALUES (?,?,?,?)",
                            (time.time(), level, source, message))
            self.db.commit()

    def events(self, limit: int = 300) -> list[dict]:
        with self.lock:
            cur = self.db.execute(
                "SELECT ts, level, source, message FROM events ORDER BY ts DESC LIMIT ?", (limit,))
            return [{"ts": t, "level": l, "source": s, "message": m} for t, l, s, m in cur]

    def stats(self) -> dict:
        with self.lock:
            n, t0 = self.db.execute("SELECT COUNT(*), MIN(ts) FROM snapshots").fetchone()
        return {"snapshot_rows": n, "since": t0}


DEFAULT_SETTINGS = {
    "poll_seconds": 5,
    "snapshot_seconds": 60,
    "horizon_hours": 72,
    "exec_mode": "maker",          # maker | taker -- which fee the opportunity table charges
    "min_volume_usd": 100_000,
    "enabled_venues": ["hyperliquid", "arcus", "bulk", "reya", "meridian"],
    "fee_overrides": {},           # {"bulk": {"maker": 0, "taker": 0.00035}}
}


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.data = dict(DEFAULT_SETTINGS)
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text()))
            except Exception:
                pass

    def update(self, patch: dict) -> dict:
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                self.data[k] = v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))
        return self.data

    def __getitem__(self, k):
        return self.data[k]
