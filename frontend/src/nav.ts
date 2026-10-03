export type NavItem = { id: string; label: string; m?: 2 | 3 };
export type NavSection = { id: string; label: string; icon: string; items: NavItem[] };

// `m` marks pages whose function arrives in a later milestone (shown, but labelled).
export const NAV: NavSection[] = [
  { id: "overview", label: "Overview", icon: "overview", items: [
    { id: "summary", label: "Summary" },
    { id: "health", label: "Venue health" },
  ]},
  { id: "venues", label: "Venues", icon: "venues", items: [
    { id: "hyperliquid", label: "Hyperliquid" },
    { id: "arcus", label: "Arcus" },
    { id: "bulk", label: "Bulk" },
    { id: "reya", label: "Reya" },
    { id: "meridian", label: "Meridian" },
  ]},
  { id: "markets", label: "Markets", icon: "markets", items: [
    { id: "funding", label: "Funding matrix" },
    { id: "prices", label: "Price matrix" },
    { id: "spot", label: "Hyperliquid spot" },
  ]},
  { id: "opportunities", label: "Opportunities", icon: "opps", items: [
    { id: "cross", label: "Cross-venue funding" },
    { id: "carry", label: "Spot-perp carry" },
  ]},
  { id: "strategies", label: "Strategies", icon: "strategies", items: [
    { id: "funding-arb", label: "Funding arb" },
    { id: "spot-carry", label: "Spot carry" },
  ]},
  { id: "orders", label: "Orders & positions", icon: "orders", items: [
    { id: "new", label: "New order" },
    { id: "positions", label: "Positions" },
    { id: "open", label: "Open orders" },
    { id: "history", label: "Trade history" },
  ]},
  { id: "risk", label: "Risk", icon: "risk", items: [
    { id: "limits", label: "Limits" },
    { id: "brakes", label: "Brakes" },
    { id: "kill", label: "Kill switch" },
  ]},
  { id: "logs", label: "Logs", icon: "logs", items: [
    { id: "events", label: "Events" },
  ]},
  { id: "settings", label: "Settings", icon: "settings", items: [
    { id: "general", label: "General" },
    { id: "fees", label: "Fees" },
    { id: "venues", label: "Venues" },
    { id: "keys", label: "API keys" },
  ]},
];

export const DEFAULT_ROUTE = "overview/summary";

export function findRoute(path: string) {
  const [s, i] = path.split("/");
  const sec = NAV.find((x) => x.id === s);
  const item = sec?.items.find((x) => x.id === i);
  return sec && item ? { sec, item } : null;
}
