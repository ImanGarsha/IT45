"""Market feed: polls every enabled venue concurrently, keeps the latest quotes and
per-venue health, snapshots to SQLite. A venue that fails keeps its LAST quotes but
is marked stale -- the dashboard shows age, and M2's stale-data brake reads `age_s`.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from .models import Quote
from .store import Settings, Store
from .venues import VENUES

STALE_AFTER_S = 30


class Feed:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.client = httpx.AsyncClient(timeout=12, headers={"User-Agent": "IT45/0.1"})
        self.venues = {n: V(self.client) for n, V in VENUES.items()}
        self.quotes: dict[str, list[Quote]] = {n: [] for n in VENUES}
        self.health: dict[str, dict] = {
            n: {"ok": False, "last_ok": None, "last_try": None, "latency_ms": None,
                "error": None, "markets": 0, "fails": 0} for n in VENUES}
        self._last_snapshot = 0.0
        self._task: asyncio.Task | None = None
        self.cycles = 0
        self.listeners: list = []      # async callables run after every cycle (trading engine)

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
        await self.client.aclose()

    async def _loop(self):
        self.store.log("info", "feed", "feed started")
        while True:
            try:
                await self.poll_once()
            except Exception as e:  # never let the loop die
                self.store.log("error", "feed", f"cycle crashed: {e!r}")
            await asyncio.sleep(max(1, float(self.settings["poll_seconds"])))

    # ---- one cycle -------------------------------------------------------
    async def poll_once(self):
        enabled = [n for n in self.venues if n in self.settings["enabled_venues"]]
        await asyncio.gather(*(self._poll(n) for n in enabled))
        self.cycles += 1
        now = time.time()
        if now - self._last_snapshot >= float(self.settings["snapshot_seconds"]):
            self._last_snapshot = now
            fresh = [q for n in enabled if self.health[n]["ok"] for q in self.quotes[n]]
            if fresh:
                self.store.save_snapshot(fresh)
        for fn in self.listeners:
            try:
                await fn()
            except Exception as e:
                self.store.log("error", "engine", f"cycle listener failed: {e!r}")

    async def _poll(self, name: str):
        h = self.health[name]
        if time.time() < h.get("backoff_until", 0):
            return                      # rate-limited: wait it out instead of hammering
        t0 = time.time()
        h["last_try"] = t0
        try:
            qs = await self.venues[name].fetch()
            self._apply_fee_overrides(name, qs)
            self.quotes[name] = qs
            was_down = not h["ok"] and h["fails"] > 0
            h.update(ok=True, last_ok=time.time(), latency_ms=(time.time() - t0) * 1000,
                     error=None, markets=len(qs), fails=0)
            if was_down:
                self.store.log("info", name, "recovered")
        except Exception as e:
            h["fails"] += 1
            msg = f"{type(e).__name__}: {e}".split("\n")[0][:300]
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                wait = min(60.0, 5.0 * 2 ** (h["fails"] - 1))
                h["backoff_until"] = time.time() + wait
                msg = f"rate limited (HTTP 429), backing off {wait:.0f}s"
            if h["ok"] or h["fails"] == 1 or h["error"] != msg:
                self.store.log("warn", name, f"poll failed: {msg}")
            h.update(ok=False, error=msg)

    def _apply_fee_overrides(self, name, qs):
        ov = (self.settings["fee_overrides"] or {}).get(name)
        if not ov:
            return
        for q in qs:
            if "maker" in ov:
                q.maker_fee = float(ov["maker"])
            if "taker" in ov:
                q.taker_fee = float(ov["taker"])

    # ---- views -----------------------------------------------------------
    def all_quotes(self, include_stale: bool = True) -> list[Quote]:
        out = []
        for n, qs in self.quotes.items():
            if n not in self.settings["enabled_venues"]:
                continue
            if not include_stale and self.age(n) > STALE_AFTER_S:
                continue
            out += qs
        return out

    def age(self, name) -> float:
        lo = self.health[name]["last_ok"]
        return float("inf") if lo is None else time.time() - lo

    def venue_status(self) -> list[dict]:
        out = []
        for n, v in self.venues.items():
            h = self.health[n]
            age = self.age(n)
            out.append({**v.info(), **h,
                        "enabled": n in self.settings["enabled_venues"],
                        "age_s": None if age == float("inf") else age,
                        "stale": age > STALE_AFTER_S,
                        "perps": sum(q.kind == "perp" for q in self.quotes[n]),
                        "spot": sum(q.kind == "spot" for q in self.quotes[n])})
        return out
