"""The gate every order passes through. Boring, ordered, fails closed (ported from IT4-2).

Checks, in order:
 1. kill switch           -- refuses everything except reduce-only orders
 2. venue mode            -- off refuses; testnet needs a connector; mainnet does not exist in M2
 3. venue halted          -- stale feed or too many consecutive API errors (brakes)
 4. price sanity          -- a fresh book must exist and the limit must be within slippage of it
 5. order notional        -- per-order cap
 6. venue gross           -- per-venue cap after the order
 7. book gross            -- all-venue cap after the order
 8. daily loss            -- once hit, only reduce-only orders pass
Reduce-only orders skip 5-8 (closing risk must always be possible) but not 1-4's mechanics,
except the kill switch, which exists precisely to let closing orders through.
Every decision is written to the `audit` table.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Decision:
    ok: bool
    reason: str = "ok"
    checks: list = field(default_factory=list)


class Guard:
    def __init__(self, db, cfg_getter):
        self.db = db
        self.cfg = cfg_getter
        self.halted: dict[str, str] = {}        # venue -> reason (set by brakes)
        self.daily_loss_hit = False

    def check(self, *, venue: str, mode: str, symbol: str, side: str, notional: float,
              price: float, book_mid: float | None, book_age_s: float | None, reduce_only: bool,
              venue_gross: float, book_gross: float, record: bool = True) -> Decision:
        c = self.cfg()
        checks = []

        def step(name, ok, detail):
            checks.append({"check": name, "ok": ok, "detail": detail})
            return ok

        def no(reason):
            d = Decision(False, reason, checks)
            if not record:
                return d
            self.db.audit("order_refused", venue, symbol, False, {"reason": reason, "side": side,
                                                                  "notional": notional, "checks": checks})
            return d

        if c["kill_switch"] and not reduce_only:
            step("kill_switch", False, "engaged")
            return no("Kill switch is engaged: only closing orders are allowed.")
        step("kill_switch", True, "released" if not c["kill_switch"] else "engaged, reduce-only passes")

        vm = c["venue_modes"].get(venue, "off")
        if vm == "off" or vm != mode:
            step("mode", False, f"venue mode {vm}, broker {mode}")
            return no(f"{venue} is set to '{vm}' -- this {mode} order is not allowed.")
        step("mode", True, vm)

        if venue in self.halted and not reduce_only:
            step("venue_halted", False, self.halted[venue])
            return no(f"{venue} is halted: {self.halted[venue]}")
        step("venue_halted", True, "ok")

        if not book_mid or book_mid <= 0:
            step("price", False, "no book")
            return no("No live book for this market.")
        stale = float(c["brakes"]["stale_s"])
        if book_age_s is not None and book_age_s > stale:
            step("price", False, f"book {book_age_s:.0f}s old")
            return no(f"Book is {book_age_s:.0f}s old (limit {stale:.0f}s).")
        # only the AGGRESSIVE side is checked: a buy far above mid (or a sell far below) would pay
        # through the market; a buy resting below mid is a harmless limit order
        dev_bp = ((price - book_mid) if side == "buy" else (book_mid - price)) / book_mid * 1e4
        max_dev = float(c["exec"]["max_slippage_bp"]) + 50
        if dev_bp > max_dev:
            step("price", False, f"limit {dev_bp:.0f} bp from mid")
            return no(f"Limit price is {dev_bp:.0f} bp through mid (max {max_dev:.0f}): it would pay far beyond the market.")
        step("price", True, f"{dev_bp:.1f} bp from mid")

        if not reduce_only:
            caps = c["caps"]
            if notional > float(caps["max_order_usd"]) + 1e-9:
                step("max_order_usd", False, f"${notional:,.2f}")
                return no(f"Order ${notional:,.2f} exceeds the per-order cap ${caps['max_order_usd']:,.0f}.")
            step("max_order_usd", True, f"${notional:,.2f}")
            if venue_gross + notional > float(caps["max_venue_usd"]) + 1e-9:
                step("max_venue_usd", False, f"${venue_gross + notional:,.2f}")
                return no(f"{venue} gross would be ${venue_gross + notional:,.2f} (cap ${caps['max_venue_usd']:,.0f}).")
            step("max_venue_usd", True, f"${venue_gross + notional:,.2f}")
            if book_gross + notional > float(caps["max_gross_usd"]) + 1e-9:
                step("max_gross_usd", False, f"${book_gross + notional:,.2f}")
                return no(f"Book gross would be ${book_gross + notional:,.2f} (cap ${caps['max_gross_usd']:,.0f}).")
            step("max_gross_usd", True, f"${book_gross + notional:,.2f}")
            if self.daily_loss_hit:
                step("daily_loss", False, "limit hit today")
                return no("Daily loss limit hit: only closing orders until tomorrow (UTC).")
            step("daily_loss", True, "ok")

        if record:
            self.db.audit("order_allowed", venue, symbol, True, {"side": side, "notional": notional,
                                                                 "reduce_only": reduce_only})
        return Decision(True, "ok", checks)
