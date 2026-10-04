"""Strategies: cross-venue funding arb and Hyperliquid spot-perp carry.

Both are the same machine with different candidate sets:
  ENTRY  current edge >= entry_edge_apr  AND  lookback mean >= entry_edge_apr
         AND  share of positive samples >= min_frac_positive  AND  n >= min_samples
         AND  taker-fee breakeven <= max_breakeven_h  AND  volume, venue-mode and conflict filters
  EXIT   (any) 1h mean edge < exit_edge_apr | held > max_hold_h |
         leg prices diverged > max_divergence_bp from entry | a better pair beats this one by
         more than the switching fees over the horizon (rotate)
A (venue, symbol, kind) can belong to only one open pair, so a pair's legs ARE the positions.
"""
from __future__ import annotations

import time

from .. import opportunities as opp
from ..models import HOURS_PER_YEAR
from .db import new_id
from .executor import Leg
from .persistence import persistence


class PairStrategy:
    name = ""

    def __init__(self, engine):
        self.e = engine
        self.last_eval = 0.0
        self.busy = False
        self.last_candidates: list[dict] = []
        self.last_note = ""
        self.cooldown: dict[tuple, float] = {}   # candidate -> until (after a refused/failed open)

    def scfg(self) -> dict:
        return self.e.cfg()["strategies"][self.name]

    # ------------------------------------------------------------- candidates
    def raw(self, quotes, horizon) -> list:
        raise NotImplementedError

    def _usable(self, venue: str) -> bool:
        c = self.e.cfg()
        return c["venue_modes"].get(venue, "off") != "off" and venue not in self.e.guard.halted

    def _taken(self) -> set:
        # every leg already held -- by a pair or by a manual order -- is off limits, because a
        # strategy pair assumes its legs ARE the venue positions
        t = {(p.venue, p.symbol, p.kind) for p in self.e.last_positions if abs(p.size) > 1e-12}
        for p in self.e.db.open_pairs():
            t.add((p["long_venue"], p["symbol"], p["long_kind"]))
            t.add((p["short_venue"], p["symbol"], p["short_kind"]))
        return t

    def candidates(self) -> list[dict]:
        s = self.scfg()
        quotes = self.e.feed.all_quotes(include_stale=False)
        taken = self._taken()
        out = []
        for o in self.raw(quotes, 72):
            if not (self._usable(o.long_venue) and self._usable(o.short_venue)):
                continue
            if o.min_volume_usd < float(s["min_volume_usd"]):
                continue
            if not s["allow_rwa"] and any("RWA" in f or "mPerp" in f for f in o.flags):
                continue
            if any("extreme rate" in f or "price gap" in f for f in o.flags):
                continue
            if o.edge_apr < float(s["entry_edge_apr"]):
                continue
            if o.breakeven_h is None or o.breakeven_h > float(s["max_breakeven_h"]):
                continue
            if (o.long_venue, o.symbol, o.long_leg) in taken or (o.short_venue, o.symbol, "perp") in taken:
                continue
            if self.cooldown.get((o.symbol, o.long_venue, o.long_leg, o.short_venue), 0) > time.time():
                continue
            per = persistence(self.e.store, o.symbol, o.long_venue, o.long_leg, o.short_venue,
                              float(s["lookback_h"]))
            d = o.to_dict()
            d["persistence"] = per.to_dict()
            d["eligible"] = (per.n >= int(s["min_samples"]) and per.mean_edge_apr is not None
                             and per.mean_edge_apr >= float(s["entry_edge_apr"])
                             and per.frac_positive >= float(s["min_frac_positive"]))
            if not d["eligible"]:
                d["why_not"] = (f"only {per.n} samples (need {s['min_samples']})" if per.n < int(s["min_samples"])
                                else f"{s['lookback_h']}h mean {100 * (per.mean_edge_apr or 0):.1f}%/yr, "
                                     f"positive {100 * (per.frac_positive or 0):.0f}% of the time")
            out.append(d)
        out.sort(key=lambda d: (d["eligible"], (d["persistence"]["mean_edge_apr"] or -9)), reverse=True)
        return out

    # ------------------------------------------------------------- live edge
    def current_edge_apr(self, p: dict) -> float | None:
        def fr(venue, kind):
            for q in self.e.feed.quotes.get(venue, []):
                if q.symbol == p["symbol"] and q.kind == kind:
                    return q
            return None
        s, l = fr(p["short_venue"], "perp"), fr(p["long_venue"], p["long_kind"])
        if not s or s.funding_1h is None or not l:
            return None
        lf = 0.0 if p["long_kind"] == "spot" else (l.funding_1h or 0.0)
        return (s.funding_1h - lf) * HOURS_PER_YEAR

    def basis_bp(self, p: dict) -> float | None:
        bl = self.e.paper_or_live_mid(p["long_venue"], p["symbol"], p["long_kind"])
        bs = self.e.paper_or_live_mid(p["short_venue"], p["symbol"], "perp")
        if not bl or not bs:
            return None
        return (bs - bl) / ((bs + bl) / 2) * 1e4

    # ------------------------------------------------------------- the loop
    async def evaluate(self):
        s = self.scfg()
        if self.busy or time.time() - self.last_eval < float(s["eval_s"]):
            return
        self.busy = True
        self.last_eval = time.time()
        try:
            await self._exits()
            if not s["enabled"] or self.e.cfg()["kill_switch"] or self.e.guard.daily_loss_hit:
                self.last_candidates = self.candidates() if s["enabled"] else []
                return
            cands = self.candidates()
            self.last_candidates = cands
            mine = [p for p in self.e.db.open_pairs() if p["strategy"] == self.name]
            total_open = len(self.e.db.open_pairs())
            elig = [c for c in cands if c["eligible"]]
            if not cands:
                self.last_note = ("no spread passes the entry filters (edge, taker-fee breakeven, volume, "
                                  "venue modes)")
                return
            if not elig:
                self.last_note = f"{len(cands)} spread(s) above entry, none passes the persistence test yet"
                return
            self.last_note = f"{len(elig)} eligible"
            if total_open < int(self.e.cfg()["caps"]["max_pairs"]):
                await self.open_pair(elig[0])
            elif s["rotate"] and mine:
                await self._maybe_rotate(mine, elig[0])
        except Exception as ex:  # a strategy bug must not kill the engine
            self.e.db.audit("strategy_error", "", "", False, {"strategy": self.name, "error": repr(ex)})
            self.last_note = f"error: {ex!r}"
        finally:
            self.busy = False

    async def _exits(self):
        s = self.scfg()
        for p in self.e.db.open_pairs():
            if p["strategy"] != self.name or p["status"] != "open":
                continue
            if not (self.e.settled(p["long_venue"]) and self.e.settled(p["short_venue"])):
                continue          # never exit on stale or missing data during an outage
            reason = None
            per = persistence(self.e.store, p["symbol"], p["long_venue"], p["long_kind"],
                              p["short_venue"], 1.0)
            edge = per.mean_edge_apr if per.n >= 20 else self.current_edge_apr(p)
            if edge is not None and edge < float(s["exit_edge_apr"]):
                reason = f"edge {100 * edge:.1f}%/yr below exit {100 * float(s['exit_edge_apr']):.1f}%"
            elif time.time() - p["opened_ts"] > float(s["max_hold_h"]) * 3600:
                reason = f"held longer than {s['max_hold_h']} h"
            else:
                b = self.basis_bp(p)
                if b is not None and p["entry_basis_bp"] is not None and \
                        abs(b - p["entry_basis_bp"]) > float(s["max_divergence_bp"]):
                    reason = f"leg prices diverged {b - p['entry_basis_bp']:+.0f} bp since entry"
            if reason:
                await self.close_pair(p, reason)

    async def _maybe_rotate(self, mine, best):
        horizon = 72.0
        worst = min(mine, key=lambda p: self.current_edge_apr(p) or 0)
        cur = self.current_edge_apr(worst) or 0
        gain = (best["persistence"]["mean_edge_apr"] - cur) * horizon / HOURS_PER_YEAR
        cost = 2 * best["cost_bp"] / 1e4        # open new + close old, approx equal fee legs
        if gain > cost:
            await self.close_pair(worst, f"rotated to {best['symbol']} {best['long_venue']}/"
                                         f"{best['short_venue']} (+{100 * gain:.2f}% over {horizon:.0f} h)")
            await self.open_pair(best)

    # ------------------------------------------------------------- actions
    async def open_pair(self, c: dict):
        caps = self.e.cfg()["caps"]
        # 2% headroom: the cap is checked at mid and prices move between sizing and sending
        usd = 0.98 * min(float(caps["max_pair_usd"]), float(caps["max_order_usd"]))
        mid = self.e.paper_or_live_mid(c["long_venue"], c["symbol"], c["long_leg"])
        if not mid:
            return
        size = usd / mid
        legs = [Leg(c["long_venue"], c["symbol"], c["long_leg"], "buy", size),
                Leg(c["short_venue"], c["symbol"], "perp", "sell", size)]
        key = (c["symbol"], c["long_venue"], c["long_leg"], c["short_venue"])
        why = await self.e.executor.preflight(legs)
        if why:   # caps/brakes say no: skip quietly for 30 minutes, record nothing as a 'pair'
            self.cooldown[key] = time.time() + 1800
            self.last_note = f"{c['symbol']} {c['long_venue']}/{c['short_venue']} skipped: {why}"
            return
        pid = new_id("pair")
        self.e.db.insert_pair({"id": pid, "strategy": self.name, "symbol": c["symbol"],
                               "long_venue": c["long_venue"], "long_kind": c["long_leg"],
                               "short_venue": c["short_venue"], "short_kind": "perp", "target_usd": usd,
                               "status": "opening", "opened_ts": time.time(),
                               "entry_edge_apr": c["persistence"]["mean_edge_apr"],
                               "entry_basis_bp": c["basis_bp"], "notes": ""})
        self.e.db.audit("pair_open", "", c["symbol"], True, {"pair": pid, "candidate": c})
        ra, rb = await self.e.executor.run_pair(legs, pid)
        if ra.filled == 0 and rb.filled == 0:
            why = ra.error if ra.error == rb.error else f"{ra.error or ''} {rb.error or ''}".strip()
            self.e.db.update_pair(pid, status="failed", closed_ts=time.time(), close_reason=f"not opened: {why}")
            # do not retry the same pair every evaluation: wait 30 minutes
            self.cooldown[key] = time.time() + 1800
            return
        basis = self.basis_bp({"symbol": c["symbol"], "long_venue": c["long_venue"], "long_kind": c["long_leg"],
                               "short_venue": c["short_venue"]})
        self.e.db.update_pair(pid, status="open", entry_basis_bp=basis,
                              notes=f"filled long {ra.filled:.6g} @ {ra.avg_px or 0:.6g}, "
                                    f"short {rb.filled:.6g} @ {rb.avg_px or 0:.6g}; fees ${ra.fees + rb.fees:.4f}")

    async def close_pair(self, p: dict, reason: str):
        self.e.db.update_pair(p["id"], status="closing", close_reason=reason)
        self.e.db.audit("pair_close", "", p["symbol"], True, {"pair": p["id"], "reason": reason})
        lp = await self.e.position(p["long_venue"], p["symbol"], p["long_kind"])
        sp = await self.e.position(p["short_venue"], p["symbol"], "perp")
        legs = []
        if lp and lp.size > 0:
            legs.append(Leg(p["long_venue"], p["symbol"], p["long_kind"], "sell", abs(lp.size), True))
        if sp and sp.size < 0:
            legs.append(Leg(p["short_venue"], p["symbol"], "perp", "buy", abs(sp.size), True))
        if len(legs) == 2:
            await self.e.executor.run_pair(legs, p["id"])
        for leg in legs if len(legs) == 1 else []:
            await self.e.executor.ioc(leg, p["id"])
        lp = await self.e.position(p["long_venue"], p["symbol"], p["long_kind"])
        sp = await self.e.position(p["short_venue"], p["symbol"], "perp")
        if (not lp or abs(lp.size) < 1e-12) and (not sp or abs(sp.size) < 1e-12):
            self.e.db.update_pair(p["id"], status="closed", closed_ts=time.time())
        # else stays 'closing'; the risk pass retries closing it with IOC


class FundingArb(PairStrategy):
    name = "funding_arb"

    def raw(self, quotes, horizon):
        return opp.cross_venue(quotes, horizon_h=horizon, mode="taker", min_volume_usd=0)


class SpotCarry(PairStrategy):
    name = "spot_carry"

    def raw(self, quotes, horizon):
        return opp.spot_carry(quotes, horizon_h=horizon, mode="taker", min_volume_usd=0)
