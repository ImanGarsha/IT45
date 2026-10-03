// Lists every visible element on every page that has no hover help.
//   node scripts/help_audit.mjs [http://127.0.0.1:8045]    (needs `npm i playwright` somewhere on NODE_PATH)
import { chromium } from "playwright";
const base = process.argv[2] || "http://127.0.0.1:8045";
const routes = ["overview/summary", "overview/health", "venues/hyperliquid", "venues/bulk", "markets/funding",
  "markets/funding?BTC", "markets/prices", "markets/spot", "opportunities/cross", "opportunities/carry",
  "strategies/funding-arb", "strategies/spot-carry", "orders/new", "orders/new|limit", "orders/new|review", "orders/positions", "orders/open", "orders/history",
  "risk/limits", "risk/brakes", "risk/kill", "logs/events", "settings/general", "settings/fees",
  "settings/venues", "settings/keys"];
const b = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined }).catch(() => chromium.launch());
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const missing = new Map();
let checked = 0;
// "route|state": after loading, click through to a state that shows more controls.
const states = {
  limit: ["Limit", "Bid"],
  review: ["Review order", "Review pair"],
};
for (const r of routes) {
  const [route, state] = r.split("|");
  await p.goto(`${base}/#/${route}`);
  await p.reload();
  await p.waitForTimeout(2500);
  for (const name of states[state] || []) {
    await p.getByRole("button", { name, exact: true }).first().click().catch(() => {});
    await p.waitForTimeout(300);
  }
  // open every menu section so all nav items are visible
  for (const s of await p.$$(".menu-sec[aria-expanded=false]")) await s.click();
  const res = await p.evaluate(() => {
    const sel = "th, label, button, .stat, h1, h2, h3, dt, .menu a, .vd, .mode-pill, .vc-row, tbody tr:first-child td, select, input";
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const h = window.__it45Help(el);
      const txt = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || "").trim().slice(0, 60);
      if (!h && txt) out.push(`${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ")[0] : ""}: ${txt}`);
    }
    return { n: document.querySelectorAll(sel).length, out };
  });
  checked += res.n;
  for (const m of res.out) missing.set(m, (missing.get(m) || []).concat(r));
}
console.log(`checked ${checked} elements on ${routes.length} pages; ${missing.size} without help`);
for (const [m, rs] of missing) console.log(`  ${m}   [${rs[0]}${rs.length > 1 ? ` +${rs.length - 1}` : ""}]`);
await b.close();
