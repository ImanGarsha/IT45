"""Brakes and the kill switch. Run once per feed cycle, independent of the strategies.

1. stale feed / API errors -> venue halted (no new orders there; closing still allowed)
2. daily loss              -> flatten everything, engage the kill switch, stop strategies
3. hedge imbalance         -> a pair whose legs differ by more than imbalance_pct for longer
                              than imbalance_timeout_s is repaired (lone leg: closed)
4. liquidation distance    -> a leg closer than min_liq_distance to liquidation: pair halved
None of 2-4 acts on a venue that is unreachable or reconnected less than Engine.SETTLE_S ago:
an outage is never read as a loss, a lone leg or a broken hedge.
Also retries closing any pair stuck in 'closing'.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .executor import Leg


class Risk:
    def __init__(self, engine):
        self.e = engine
        self.day = None
        self.day_start_equity: float | None = None
        self.imbalance_since: dict[str, float] = {}
        self.busy = False
        self.state: dict = {}

    async def run(self):
        if self.busy:
            return
        self.busy = True
        try:
            await self.e.retry_unavailable()
            await self.e.probe_venues()
            self.e.last_positions = await self.e.all_positions()
            await self._stale()
            await self._daily_loss()
            await self._pairs()
        except Exception as ex:
            self.e.db.audit("risk_error", "", "", False, repr(ex))
        finally:
            self.busy = False

    # 1 -----------------------------------------------------------------------
    async def _stale(self):
        c = self.e.cfg()
        g = self.e.guard
        for v, mode in c["venue_modes"].items():
            if mode == "off":
                continue
            age = self.e.feed.age(v)
            stale = age > float(c["brakes"]["stale_s"])
            if stale and v not in g.halted:
                g.halted[v] = f"market feed stale ({age:.0f}s)" if age != float("inf") else "no market feed"
                self.e.db.audit("venue_halted", v, "", False, g.halted[v])
            elif not stale and g.halted.get(v, "").startswith(("market feed", "no market feed")):
                g.halted.pop(v)
                self.e.db.audit("venue_resumed", v, "", True, "feed fresh again")

    # 2 -----------------------------------------------------------------------
    async def _daily_loss(self):
        today = datetime.now(timezone.utc).date()
        if not self.e.equity_trustworthy():
            # an outage is not a loss: equity cannot be measured while an account cannot be read
            self.state = {**self.state, "paused": "account not readable: " + ", ".join(
                sorted(v for v, w in self.e.unreachable.items() if not w.startswith("no fresh"))
                or ["just reconnected"])}
            return
        self.state.pop("paused", None)
        eq = await self.e.total_equity()
        if eq is None:
            return
        if self.day != today:
            self.day, self.day_start_equity = today, eq
            self.e.guard.daily_loss_hit = False
        start_cap = await self.e.total_start_capital()
        limit = float(self.e.cfg()["brakes"]["daily_loss_pct"]) * start_cap
        pnl = eq - self.day_start_equity
        self.state = {"day": str(today), "day_start_equity": self.day_start_equity, "equity": eq,
                      "pnl_today": pnl, "limit": -limit}
        if pnl <= -limit and not self.e.guard.daily_loss_hit:
            self.e.guard.daily_loss_hit = True
            self.e.db.audit("daily_loss_brake", "", "", False, self.state)
            await self.e.kill(f"daily loss ${pnl:,.2f} hit the limit ${-limit:,.2f}")

    # 3 + 4 -------------------------------------------------------------------
    async def _pairs(self):
        b = self.e.cfg()["brakes"]
        for p in self.e.db.open_pairs():
            if not (self.e.settled(p["long_venue"]) and self.e.settled(p["short_venue"])):
                # positions on an unreachable venue are unknown, not gone: leave the pair alone and
                # restart its imbalance clock once both venues are back
                self.imbalance_since.pop(p["id"], None)
                continue
            lp = await self.e.position(p["long_venue"], p["symbol"], p["long_kind"])
            sp = await self.e.position(p["short_venue"], p["symbol"], "perp")
            ln = lp.notional if lp and lp.size > 0 else 0.0
            sn = sp.notional if sp and sp.size < 0 else 0.0
            if p["status"] == "closing":
                await self._finish_close(p, lp, sp)
                continue
            if p["status"] != "open":
                continue
            big = max(ln, sn)
            if big == 0:
                self.e.db.update_pair(p["id"], status="closed", closed_ts=time.time(),
                                      close_reason=p["close_reason"] or "legs found flat")
                continue
            if abs(ln - sn) / big > float(b["imbalance_pct"]):
                t0 = self.imbalance_since.setdefault(p["id"], time.time())
                if time.time() - t0 > float(b["imbalance_timeout_s"]):
                    self.imbalance_since.pop(p["id"], None)
                    await self._rebalance(p, lp, sp, ln, sn)
            else:
                self.imbalance_since.pop(p["id"], None)
            for leg in (lp, sp):
                if leg and leg.liq_distance is not None and leg.liq_distance < float(b["min_liq_distance"]):
                    self.e.db.audit("liq_brake", leg.venue, leg.symbol, False,
                                    {"pair": p["id"], "distance": leg.liq_distance})
                    await self._halve(p, lp, sp)
                    break

    async def _rebalance(self, p, lp, sp, ln, sn):
        ex = self.e.executor
        if ln == 0 or sn == 0:     # lone leg: close it
            lone = lp if ln else sp
            side = "sell" if lone.size > 0 else "buy"
            r = await ex.ioc(Leg(lone.venue, lone.symbol, lone.kind, side, abs(lone.size), True), p["id"])
            self.e.db.update_pair(p["id"], status="closed" if r.filled else "closing", closed_ts=time.time(),
                                  close_reason="imbalance brake: lone leg closed")
            return
        if ln > sn:
            excess = (ln - sn) / lp.mark
            await ex.ioc(Leg(lp.venue, lp.symbol, lp.kind, "sell", excess, True), p["id"])
        else:
            excess = (sn - ln) / sp.mark
            await ex.ioc(Leg(sp.venue, sp.symbol, "perp", "buy", excess, True), p["id"])
        self.e.db.audit("imbalance_brake", "", p["symbol"], True, {"pair": p["id"], "long": ln, "short": sn})

    async def _halve(self, p, lp, sp):
        legs = []
        if lp and lp.size > 0:
            legs.append(Leg(lp.venue, lp.symbol, lp.kind, "sell", lp.size / 2, True))
        if sp and sp.size < 0:
            legs.append(Leg(sp.venue, sp.symbol, "perp", "buy", abs(sp.size) / 2, True))
        for leg in legs:
            await self.e.executor.ioc(leg, p["id"])

    async def _finish_close(self, p, lp, sp):
        for pos, side in ((lp, "sell"), (sp, "buy")):
            if pos and abs(pos.size) > 1e-12:
                await self.e.executor.ioc(Leg(pos.venue, pos.symbol, pos.kind, side, abs(pos.size), True), p["id"])
        lp = await self.e.position(p["long_venue"], p["symbol"], p["long_kind"])
        sp = await self.e.position(p["short_venue"], p["symbol"], "perp")
        if (not lp or abs(lp.size) < 1e-12) and (not sp or abs(sp.size) < 1e-12):
            self.e.db.update_pair(p["id"], status="closed", closed_ts=time.time())
