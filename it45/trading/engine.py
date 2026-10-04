"""Trading engine: wires feed -> brokers -> risk -> strategies, and exposes actions to the API."""
from __future__ import annotations

import asyncio
import os
import time

from . import config as tcfg
from .brokers import PaperBroker, Position
from .db import TradeDB
from .executor import Executor, Leg
from .guard import Guard
from .manual import Manual
from .risk import Risk
from .strategies import FundingArb, SpotCarry


class ConfigError(ValueError):
    pass


class Unavailable:
    """Stands in for a connector that failed to start: refuses every order, shows why."""

    def __init__(self, venue, mode, why):
        self.venue, self.mode, self.why = venue, mode, why

    async def book(self, symbol, kind):
        return None

    def cached_mid(self, symbol, kind):
        return None

    async def place(self, **kw):
        from .brokers import Ack
        return Ack("-", "rejected", error=f"connector unavailable: {self.why}")

    async def status(self, order_id):
        from .brokers import Ack
        return Ack(order_id, "rejected", error=self.why)

    cancel = status

    async def cancel_all(self):
        return 0

    async def positions(self):
        return []

    async def account(self):
        return {"venue": self.venue, "mode": self.mode, "error": self.why}

    def tick(self, dt):
        pass


class Engine:
    def __init__(self, feed, settings, store, hub=None):
        self.feed, self.settings, self.store = feed, settings, store
        self.hub = hub
        if hub is not None:
            hub.listeners.append(self._on_trade)
        self.db = TradeDB(store)
        self.guard = Guard(self.db, self.cfg)
        self.executor = Executor(self)
        self.risk = Risk(self)
        self.strategies = {s.name: s for s in (FundingArb(self), SpotCarry(self))}
        self.manual = Manual(self)
        self.last_positions: list = []       # refreshed by the risk pass; read by strategies
        self.brokers: dict = {}
        self.broker_errors: dict[str, str] = {}
        self.api_errors: dict[str, int] = {}
        self._last_cycle = time.time()
        self._tasks: set[asyncio.Task] = set()
        for v in feed.venues:
            self._build_broker(v)
        feed.listeners.append(self.on_cycle)

    # ------------------------------------------------------------------ config
    def cfg(self) -> dict:
        return tcfg.merged(self.settings.data.get("trading"))

    def update_cfg(self, patch: dict) -> dict:
        cur = self.cfg()
        new = tcfg.merged(_deep_merge(cur, patch))
        errs = tcfg.validate(new)
        for v, m in new["venue_modes"].items():
            if m != cur["venue_modes"].get(v) and self._has_exposure_sync(v):
                errs.append(f"{v}: close its positions before switching mode ({cur['venue_modes'][v]} -> {m})")
        if errs:
            raise ConfigError("; ".join(errs))
        self.settings.update({"trading": new})
        for v, m in new["venue_modes"].items():
            if m != cur["venue_modes"].get(v):
                self._build_broker(v)
                self.db.audit("venue_mode", v, "", True, f"{cur['venue_modes'].get(v)} -> {m}")
        return new

    def _build_broker(self, venue: str):
        mode = self.cfg()["venue_modes"].get(venue, "off")
        self.broker_errors.pop(venue, None)
        if mode == "testnet":
            try:
                if venue == "hyperliquid":
                    from .hl_testnet import HyperliquidTestnet
                    self.brokers[venue] = HyperliquidTestnet(self.db, os.environ)
                elif venue == "bulk":
                    from .bulk_testnet import BulkTestnet
                    self.brokers[venue] = BulkTestnet(self.db, os.environ, self.cfg)
                elif venue == "meridian":
                    from .meridian_testnet import MeridianTestnet
                    self.brokers[venue] = MeridianTestnet(self.db, os.environ)
                else:
                    raise RuntimeError(f"no testnet connector for {venue}")
                return
            except Exception as ex:
                # never fall back to paper silently when testnet was asked for
                self.broker_errors[venue] = str(ex)
                self.db.audit("broker_error", venue, "", False, str(ex))
                self.brokers[venue] = Unavailable(venue, mode, str(ex))
                return
        self.brokers[venue] = PaperBroker(venue, self.feed, self.db, self.cfg, hub=self.hub)

    def _has_exposure_sync(self, venue: str) -> bool:
        if any(venue in (p["long_venue"], p["short_venue"]) for p in self.db.open_pairs()):
            return True
        br = self.brokers.get(venue)
        if isinstance(br, PaperBroker):
            return bool(self.db.q("SELECT 1 FROM paper_positions WHERE venue=? AND size!=0", (venue,)))
        return False

    # ------------------------------------------------------------------ clock
    async def on_cycle(self):
        now = time.time()
        dt, self._last_cycle = now - self._last_cycle, now
        for v, br in self.brokers.items():
            if self.cfg()["venue_modes"].get(v) == "off":
                continue
            try:
                br.tick(dt)
            except Exception as ex:
                self.db.audit("tick_error", v, "", False, repr(ex))
        if self.hub is not None:
            self._spawn(self.hub.want(self.wanted_streams()))
        self._spawn(self.risk.run())
        self._spawn(self._refresh_live_orders())
        for s in self.strategies.values():
            self._spawn(s.evaluate())

    async def _reconcile_unknown(self):
        """Orders sent without a reply ('unknown'): once the venue answers again, ask it what became
        of each one. A resting one is cancelled (nobody is managing it), a fill is booked; the
        positions themselves are read from the venue, so the imbalance brake repairs any pair."""
        for o in self.db.q("SELECT id, venue FROM orders WHERE status='unknown' ORDER BY ts LIMIT 20"):
            br = self.brokers.get(o["venue"])
            if br is None or not hasattr(br, "reconcile"):
                continue
            if self.feed.age(o["venue"]) > float(self.cfg()["brakes"]["stale_s"]):
                continue          # venue still unreachable: try again next cycle
            try:
                a = await br.reconcile(o["id"])
            except Exception as ex:
                self.db.audit("reconcile_retry", o["venue"], "", False, {"order": o["id"], "error": repr(ex)})
                continue
            if a.status != "unknown":
                self.db.audit("order_reconciled", o["venue"], "", True,
                              {"order": o["id"], "status": a.status, "filled": a.filled})
                self.store.log("warn", o["venue"], f"order {o['id']} sent without a reply was found {a.status}"
                         + (f", {a.filled:g} filled" if a.filled else "") + "; resting remainder cancelled")

    async def _refresh_live_orders(self):
        """Resting orders on a real (testnet) venue only change state there: poll them."""
        await self._reconcile_unknown()
        for o in self.db.q("SELECT id, venue FROM orders WHERE status='open' AND mode='testnet' LIMIT 20"):
            br = self.brokers.get(o["venue"])
            if br is not None and br.mode == "testnet":
                try:
                    await br.status(o["id"])
                except Exception:
                    pass

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ streams
    def _on_trade(self, t):
        br = self.brokers.get(t.venue)
        if isinstance(br, PaperBroker):
            br.on_trade(t)

    def wanted_streams(self, top_n: int = 4) -> dict[str, set[str]]:
        """Live books for: legs of open pairs, resting paper orders, and both legs of the top
        candidates of every running strategy (so entries are priced off live books)."""
        want: dict[str, set[str]] = {}

        def add(v, s, k="perp"):
            if k == "perp" and self.cfg()["venue_modes"].get(v, "off") != "off":
                want.setdefault(v, set()).add(s)
        for p in self.db.open_pairs():
            add(p["long_venue"], p["symbol"], p["long_kind"])
            add(p["short_venue"], p["symbol"])
        for v, br in self.brokers.items():
            for o in getattr(br, "resting", {}).values():
                add(v, o.symbol, o.kind)
        for name, s in self.strategies.items():
            if self.cfg()["strategies"][name]["enabled"]:
                for c in s.last_candidates[:top_n]:
                    add(c["long_venue"], c["symbol"], c["long_leg"])
                    add(c["short_venue"], c["symbol"])
        for v, sym, k in self.manual.watched():     # the market open on the New order page
            add(v, sym, k)
        return want

    # ------------------------------------------------------------------ helpers
    def api_error(self, venue, msg):
        n = self.api_errors[venue] = self.api_errors.get(venue, 0) + 1
        if n >= int(self.cfg()["brakes"]["max_api_errors"]) and venue not in self.guard.halted:
            self.guard.halted[venue] = f"{n} consecutive order errors (last: {msg})"
            self.db.audit("venue_halted", venue, "", False, self.guard.halted[venue])

    def api_ok(self, venue):
        self.api_errors[venue] = 0

    def resume_venue(self, venue):
        self.guard.halted.pop(venue, None)
        self.api_errors[venue] = 0
        self.db.audit("venue_resumed", venue, "", True, "resumed from the dashboard")

    def paper_or_live_mid(self, venue, symbol, kind) -> float | None:
        br = self.brokers.get(venue)
        if br is None:
            return None
        return br.cached_mid(symbol, kind)

    async def position(self, venue, symbol, kind) -> Position | None:
        for p in await self.brokers[venue].positions():
            if p.symbol == symbol and p.kind == kind:
                return p
        return None

    async def all_positions(self) -> list[Position]:
        out = []
        for v, br in self.brokers.items():
            if self.cfg()["venue_modes"].get(v) == "off":
                continue
            try:
                out += await br.positions()
            except Exception as ex:
                self.broker_errors[v] = str(ex)
        return out

    async def venue_gross(self, venue) -> float:
        return sum(p.notional for p in await self.brokers[venue].positions())

    async def book_gross(self) -> float:
        return sum(p.notional for p in await self.all_positions())

    async def accounts(self) -> list[dict]:
        out = []
        for v, br in self.brokers.items():
            mode = self.cfg()["venue_modes"].get(v)
            if mode == "off":
                out.append({"venue": v, "mode": "off"})
                continue
            try:
                out.append(await br.account())
            except Exception as ex:
                out.append({"venue": v, "mode": mode, "error": str(ex)})
        return out

    async def total_equity(self) -> float | None:
        acc = [a for a in await self.accounts() if a.get("equity") is not None]
        return sum(a["equity"] for a in acc) if acc else None

    async def total_start_capital(self) -> float:
        acc = await self.accounts()
        return sum(a.get("start") or a.get("equity") or 0 for a in acc)

    # ------------------------------------------------------------------ actions
    async def kill(self, reason: str = "kill switch pressed"):
        c = self.cfg()
        c["kill_switch"] = True
        for s in c["strategies"].values():
            s["enabled"] = False
        self.settings.update({"trading": c})
        self.db.audit("kill_switch", "", "", False, reason)
        for p in self.db.open_pairs():
            self.db.update_pair(p["id"], status="closing", close_reason=f"kill switch: {reason}")
        for br in self.brokers.values():
            try:
                await br.cancel_all()
            except Exception as ex:
                self.db.audit("kill_cancel_error", br.venue, "", False, repr(ex))
        for _ in range(3):
            left = [p for p in await self.all_positions() if abs(p.size) > 1e-12]
            if not left:
                break
            for p in left:
                side = "sell" if p.size > 0 else "buy"
                await self.executor.ioc(Leg(p.venue, p.symbol, p.kind, side, abs(p.size), True), None)
            await asyncio.sleep(2)
        for p in self.db.open_pairs():
            self.db.update_pair(p["id"], status="closed", closed_ts=time.time())
        left = [p.to_dict() for p in await self.all_positions() if abs(p.size) > 1e-12]
        return {"reason": reason, "positions_left": left}

    def release_kill(self):
        c = self.cfg()
        c["kill_switch"] = False
        self.settings.update({"trading": c})
        self.db.audit("kill_released", "", "", True, "")

    async def close_pair(self, pair_id: str, reason="closed from the dashboard"):
        p = next((p for p in self.db.open_pairs() if p["id"] == pair_id), None)
        if not p:
            raise ConfigError("pair not open")
        closer = self.strategies.get(p["strategy"]) or self.strategies["funding_arb"]   # manual pairs
        await closer.close_pair(p, reason)

    def reset_paper(self, venue: str):
        br = self.brokers.get(venue)
        if not isinstance(br, PaperBroker):
            raise ConfigError(f"{venue} is not in paper mode")
        if self._has_exposure_sync(venue):
            raise ConfigError(f"close {venue}'s positions first")
        br.reset()
        self.db.audit("paper_reset", venue, "", True, "")

    # ------------------------------------------------------------------ views
    async def status(self) -> dict:
        c = self.cfg()
        return {
            "config": c,
            "halted": self.guard.halted,
            "daily_loss_hit": self.guard.daily_loss_hit,
            "unknown_orders": self.db.q("SELECT COUNT(*) n FROM orders WHERE status='unknown'")[0]["n"],
            "risk": self.risk.state,
            "broker_errors": self.broker_errors,
            "brokers": {v: br.mode for v, br in self.brokers.items()},
            "strategies": {n: {"enabled": c["strategies"][n]["enabled"], "note": s.last_note,
                               "last_eval": s.last_eval} for n, s in self.strategies.items()},
            "keys": {"hyperliquid": bool(os.environ.get("IT45_HL_AGENT_KEY")),
                     "bulk": bool(os.environ.get("IT45_BULK_SIGNER_KEY")),
                     "meridian": bool(os.environ.get("IT45_MERIDIAN_SIGNER_KEY"))},
            "testnet_capable": sorted(tcfg.TESTNET_CAPABLE),
        }


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _deep_merge(a[k], v) if isinstance(a.get(k), dict) and isinstance(v, dict) else v
    return out
