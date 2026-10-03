"""SQLite tables for trading (same file as market snapshots: data/it45.db)."""
from __future__ import annotations

import json
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, ts REAL, updated_ts REAL, venue TEXT, mode TEXT, symbol TEXT, kind TEXT,
    side TEXT, size REAL, limit_px REAL, post_only INTEGER, reduce_only INTEGER, status TEXT,
    filled REAL DEFAULT 0, avg_px REAL, fee REAL DEFAULT 0, pair_id TEXT, venue_order_id TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS fills (
    ts REAL, order_id TEXT, venue TEXT, mode TEXT, symbol TEXT, kind TEXT, side TEXT,
    size REAL, px REAL, fee REAL, liquidity TEXT, pair_id TEXT
);
CREATE TABLE IF NOT EXISTS pairs (
    id TEXT PRIMARY KEY, strategy TEXT, symbol TEXT, long_venue TEXT, long_kind TEXT,
    short_venue TEXT, short_kind TEXT, target_usd REAL, status TEXT, opened_ts REAL,
    closed_ts REAL, close_reason TEXT, entry_edge_apr REAL, entry_basis_bp REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS paper_positions (
    venue TEXT, symbol TEXT, kind TEXT, size REAL, avg_px REAL, realized REAL, fees REAL,
    funding REAL, PRIMARY KEY (venue, symbol, kind)
);
CREATE TABLE IF NOT EXISTS paper_cash (venue TEXT PRIMARY KEY, cash REAL, start REAL);
CREATE TABLE IF NOT EXISTS funding_payments (ts REAL, venue TEXT, mode TEXT, symbol TEXT, amount REAL);
CREATE TABLE IF NOT EXISTS audit (ts REAL, event TEXT, venue TEXT, symbol TEXT, ok INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS equity (ts REAL, venue TEXT, mode TEXT, equity REAL);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class TradeDB:
    def __init__(self, store):
        self.db, self.lock = store.db, store.lock
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # --- generic -----------------------------------------------------------
    def x(self, sql: str, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def q(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            cur = self.db.execute(sql, args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # --- orders ------------------------------------------------------------
    def insert_order(self, o: dict) -> None:
        cols = ",".join(o)
        self.x(f"INSERT INTO orders ({cols}) VALUES ({','.join('?' * len(o))})", tuple(o.values()))

    def update_order(self, oid: str, **kw) -> None:
        kw["updated_ts"] = time.time()
        sets = ",".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE orders SET {sets} WHERE id=?", (*kw.values(), oid))

    def add_fill(self, f: dict) -> None:
        self.x(f"INSERT INTO fills ({','.join(f)}) VALUES ({','.join('?' * len(f))})", tuple(f.values()))

    # --- pairs -------------------------------------------------------------
    def insert_pair(self, p: dict) -> None:
        self.x(f"INSERT INTO pairs ({','.join(p)}) VALUES ({','.join('?' * len(p))})", tuple(p.values()))

    def update_pair(self, pid: str, **kw) -> None:
        sets = ",".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE pairs SET {sets} WHERE id=?", (*kw.values(), pid))

    def open_pairs(self) -> list[dict]:
        return self.q("SELECT * FROM pairs WHERE status IN ('opening','open','closing') ORDER BY opened_ts")

    # --- audit -------------------------------------------------------------
    def audit(self, event: str, venue: str = "", symbol: str = "", ok: bool = True, detail=None):
        d = detail if isinstance(detail, str) else json.dumps(detail, default=str)
        self.x("INSERT INTO audit VALUES (?,?,?,?,?,?)",
               (time.time(), event, venue, symbol, int(ok), d))
