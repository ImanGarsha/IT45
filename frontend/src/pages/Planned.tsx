import { findRoute } from "../nav";
import { Notice, PageHead } from "../ui";

const PLAN: Record<string, { what: string; points: string[] }> = {
  "strategies/funding-arb": {
    what: "Opens a long on the venue with the cheapest funding and a short on the richest, same asset, same size.",
    points: ["Entry and exit thresholds you set here (net of fees, with a gap between them so it does not flip-flop)",
      "Exits: spread below exit threshold, maximum holding time, rotate to a better pair after switching fees, leg prices diverging",
      "Maker-first orders: post-only, re-priced a few times, then crosses if still unfilled",
      "Runs on testnets (Hyperliquid, Reya where available) and paper fills for the rest until you arm a venue"],
  },
  "strategies/spot-carry": {
    what: "Buys Hyperliquid spot and shorts the Hyperliquid perp for the same asset while funding is positive.",
    points: ["Closes when funding turns negative for longer than you allow", "Same maker-first execution and brakes as funding arb"],
  },
  "orders/positions": { what: "Every open leg across all five venues, grouped into hedged pairs with net delta, funding earned and liquidation distance.", points: [] },
  "orders/open": { what: "Resting orders on every venue, with cancel buttons.", points: [] },
  "orders/history": { what: "Fills and funding payments, stored in SQLite, exportable.", points: [] },
  "risk/limits": { what: "Capital cap per venue and per pair, leverage up to 10x per leg. Tiny defaults until you raise them.", points: [] },
  "risk/brakes": {
    what: "Automatic stops that run independently of the strategy.",
    points: ["Daily loss limit: flatten and disarm", "Lone leg: if one side fills and the other does not within N seconds, unwind it",
      "Stale feed or API errors: stop trading that venue", "Liquidation distance: reduce the leg before it gets close"],
  },
  "risk/kill": { what: "One button that cancels every order and closes every position on every venue, then disarms.", points: [] },
};

export function Planned({ route }: { route: string }) {
  const r = findRoute(route);
  const p = PLAN[route];
  return (
    <>
      <PageHead title={r?.item.label ?? "Planned"} sub={p?.what} />
      <Notice>Arrives in milestone {r?.item.m ?? 2}. This build only reads public market data: it holds no keys and cannot place orders.</Notice>
      {p && p.points.length > 0 && <ul className="plan">{p.points.map((x) => <li key={x}>{x}</li>)}</ul>}
    </>
  );
}
