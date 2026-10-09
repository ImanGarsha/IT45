"""Trading configuration: defaults, validation, and the hard limits no setting can exceed.

Stored under settings["trading"] (data/settings.json). Every value the strategies, executor,
guard and brakes read lives here, so the dashboard edits one object.
"""
from __future__ import annotations

import copy

MODES = ("off", "paper", "testnet")          # M2: no mainnet. M3 adds "mainnet" behind arming.
TESTNET_CAPABLE = {"hyperliquid", "bulk", "meridian"}   # venues with a real testnet connector
MAX_LEVERAGE = 10.0                          # PI decision: configurable up to 10x per leg

DEFAULTS: dict = {
    "kill_switch": False,
    "venue_modes": {v: "paper" for v in ("hyperliquid", "arcus", "bulk", "reya", "meridian")},
    "paper_balance_usd": 1000.0,             # starting paper collateral PER VENUE
    "leverage": 2.0,                         # per leg
    "caps": {
        "max_order_usd": 250.0,              # one order
        "max_pair_usd": 250.0,               # notional per leg of one pair
        "max_venue_usd": 600.0,              # gross notional on one venue
        "max_gross_usd": 2000.0,             # whole book, all venues
        "max_pairs": 3,                      # concurrent open pairs
    },
    "brakes": {
        "daily_loss_pct": 0.03,              # of total starting capital -> flatten + kill
        "imbalance_pct": 0.10,               # |long - short| / pair notional
        "imbalance_timeout_s": 45,           # lone-leg tolerance before unwinding
        "stale_s": 30,                       # venue feed older than this: no new orders there
        "min_liq_distance": 0.20,            # leg within 20% of its liquidation price -> reduce
        "max_api_errors": 3,                 # consecutive order errors -> venue halted
    },
    "exec": {
        "maker_attempts": 3,                 # post-only re-prices before falling back
        "reprice_s": 8,
        "taker_fallback": True,
        "max_slippage_bp": 20,               # IOC limit distance from touch
    },
    "strategies": {
        "funding_arb": {
            "enabled": False,
            "entry_edge_apr": 0.15,          # funding spread must average >= 15%/yr ...
            "exit_edge_apr": 0.03,           # ... and is closed below 3%/yr (hysteresis)
            "lookback_h": 6,                 # persistence window
            "min_frac_positive": 0.8,        # share of samples in the window with edge > 0
            "min_samples": 60,
            "max_breakeven_h": 24,           # fees must be paid back within this many hours
            "max_hold_h": 168,
            "max_divergence_bp": 150,        # close if leg prices move apart by more than this
            "rotate": True,
            "min_volume_usd": 250_000,
            "allow_rwa": False,              # stocks/indices/metals: off-hours rules not modelled
            "eval_s": 30,
        },
        "spot_carry": {
            "enabled": False,
            "entry_edge_apr": 0.12,
            "exit_edge_apr": 0.0,
            "lookback_h": 6,
            "min_frac_positive": 0.9,
            "min_samples": 60,
            "max_breakeven_h": 48,
            "max_hold_h": 336,
            "max_divergence_bp": 100,
            "rotate": False,
            "min_volume_usd": 250_000,
            "allow_rwa": False,
            "eval_s": 30,
        },
    },
}


TYPICAL_MMR = 0.025      # maintenance margin of a 20x-max market; most IT45 markets are 20-50x


def entry_liq_distance(leverage: float, mmr: float = TYPICAL_MMR) -> float:
    """How far (as a fraction of price) a NEW isolated leg starts from its liquidation price."""
    return max(0.0, 1.0 / float(leverage) - mmr)


def effective_liq_threshold(cfg: dict) -> float:
    """The liquidation brake fires below this distance. Never more than half of where a fresh leg
    starts: otherwise the brake fires the moment a pair opens and halves it again on every pass,
    paying taker fees each time (seen in the PI's paper run at 8x leverage with a 20 % brake)."""
    return min(float(cfg["brakes"]["min_liq_distance"]), 0.5 * entry_liq_distance(cfg["leverage"]))


def merged(stored: dict | None) -> dict:
    """Defaults overlaid with whatever is stored (stored keys win, unknown keys dropped)."""
    def deep(base, over):
        out = copy.deepcopy(base)
        for k, v in (over or {}).items():
            if k not in out:
                continue
            out[k] = deep(out[k], v) if isinstance(out[k], dict) and isinstance(v, dict) else v
        return out
    return deep(DEFAULTS, stored or {})


def validate(cfg: dict) -> list[str]:
    errs = []
    for v, m in cfg["venue_modes"].items():
        if m not in MODES:
            errs.append(f"{v}: mode must be one of {MODES} (mainnet arrives in M3)")
        if m == "testnet" and v not in TESTNET_CAPABLE:
            errs.append(f"{v}: no testnet connector yet -- use paper")
    if not (1.0 <= float(cfg["leverage"]) <= MAX_LEVERAGE):
        errs.append(f"leverage must be between 1 and {MAX_LEVERAGE:g}")
    for k, x in cfg["caps"].items():
        if float(x) <= 0:
            errs.append(f"caps.{k} must be > 0")
    b = cfg["brakes"]
    if not 0 < float(b["daily_loss_pct"]) <= 0.5:
        errs.append("brakes.daily_loss_pct must be in (0, 0.5]")
    if not 0 < float(b["min_liq_distance"]) < 1:
        errs.append("brakes.min_liq_distance must be in (0, 1)")
    elif 1.0 <= float(cfg["leverage"]) <= MAX_LEVERAGE:
        start = entry_liq_distance(cfg["leverage"])
        if float(b["min_liq_distance"]) >= start:
            ok_lev = int(1.0 / (float(b["min_liq_distance"]) + TYPICAL_MMR))
            errs.append(f"At {float(cfg['leverage']):g}x a new position starts only about {start:.0%} from "
                        f"liquidation, inside the {float(b['min_liq_distance']):.0%} liquidation brake: every pair "
                        f"would be cut down by taker orders as soon as it opens. Use at most {ok_lev}x with this "
                        f"brake, or set the brake below {start:.0%} (about {start / 2:.0%} is sensible).")
    for name, s in cfg["strategies"].items():
        if float(s["exit_edge_apr"]) >= float(s["entry_edge_apr"]):
            errs.append(f"{name}: exit threshold must be below entry threshold")
    return errs
