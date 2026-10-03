import { useEffect, useState } from "react";
import { Health, usePoll, VenueStatus, VENUE_ORDER } from "./api";
import { HelpLayer, useHelpEnabled } from "./HelpLayer";
import { Icon } from "./icons";
import { DEFAULT_ROUTE, findRoute, NAV } from "./nav";
import { Page } from "./pages";
import { useTrading } from "./pages/Trading";
import { Dot } from "./ui";

function useHashRoute() {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || DEFAULT_ROUTE);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

function useTheme() {
  const [theme, setTheme] = useState<string>(() => document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("it45-theme", theme); } catch { /* private mode */ }
  }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")] as const;
}

function useStored(key: string, init: boolean) {
  const [v, setV] = useState<boolean>(() => {
    try { const s = localStorage.getItem(key); return s == null ? init : s === "1"; } catch { return init; }
  });
  useEffect(() => { try { localStorage.setItem(key, v ? "1" : "0"); } catch { /* */ } }, [key, v]);
  return [v, setV] as const;
}

export default function App() {
  const route = useHashRoute().split("?")[0];
  const found = findRoute(route);
  const [theme, toggleTheme] = useTheme();
  const [help, setHelp] = useHelpEnabled();
  const [collapsed, setCollapsed] = useStored("it45-rail", false);
  const [open, setOpen] = useState<Record<string, boolean>>({ [route.split("/")[0]]: true });
  const venues = usePoll<VenueStatus[]>("/api/venues", 4000);
  const health = usePoll<Health>("/api/health", 8000);
  const trading = useTrading(5000);
  const tc = trading.data?.config;
  const modeSet = tc ? Array.from(new Set(Object.values(tc.venue_modes).filter((m) => m !== "off"))) : [];
  const pill = !tc ? "Connecting" : tc.kill_switch ? "Kill switch ON"
    : `${modeSet.map((m) => m[0].toUpperCase() + m.slice(1)).join(" + ") || "All venues off"}${Object.values(tc.strategies).some((s) => s.enabled) ? ", bot running" : ", bot stopped"}`;

  useEffect(() => { const s = route.split("/")[0]; setOpen((o) => ({ ...o, [s]: true })); }, [route]);

  const byName = Object.fromEntries((venues.data ?? []).map((v) => [v.name, v]));

  return (
    <div className={`shell ${collapsed ? "rail-collapsed" : ""}`}>
      <HelpLayer enabled={help} />
      <nav className="rail" aria-label="Main menu">
        <div className="brand">
          <span className="brand-mark">IT45</span>
          {!collapsed && <span className="brand-sub">multi-venue trader</span>}
        </div>
        <ul className="menu">
          {NAV.map((sec) => {
            const active = found?.sec.id === sec.id;
            const isOpen = !collapsed && !!open[sec.id];
            return (
              <li key={sec.id} className={active ? "active" : ""}>
                <button className="menu-sec" aria-expanded={isOpen} aria-label={sec.label}
                  onClick={() => collapsed ? (window.location.hash = `#/${sec.id}/${sec.items[0].id}`)
                    : setOpen({ ...open, [sec.id]: !open[sec.id] })}>
                  <Icon name={sec.icon} />
                  {!collapsed && <><span>{sec.label}</span><span className={`chev ${isOpen ? "open" : ""}`}><Icon name="chevron" size={14} /></span></>}
                </button>
                {isOpen && (
                  <ul className="submenu">
                    {sec.items.map((it) => {
                      const v = sec.id === "venues" ? byName[it.id] : null;
                      return (
                        <li key={it.id}>
                          <a href={`#/${sec.id}/${it.id}`}
                            aria-current={route === `${sec.id}/${it.id}` ? "page" : undefined}>
                            {v && <Dot ok={v.ok} stale={v.stale} />}
                            <span>{it.label}</span>
                            {it.m && <span className="ms">M{it.m}</span>}
                          </a>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
        <button className="rail-toggle" onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "Expand menu" : "Collapse menu"}>
          <span style={{ transform: collapsed ? "rotate(180deg)" : undefined, display: "inline-flex" }}>
            <Icon name="collapse" />
          </span>
          {!collapsed && <span>Collapse</span>}
        </button>
      </nav>

      <div className="main">
        <div className="topbar">
          <div className="crumbs">
            {found ? <>{found.sec.label}<span className="sep">/</span><b>{found.item.label}</b></> : "Not found"}
          </div>
          <div className="topbar-right">
            <div className="venue-dots">
              {VENUE_ORDER.map((n) => byName[n] && (
                <a key={n} href={`#/venues/${n}`} className="vd" data-help={`${byName[n].label} market feed: ${byName[n].ok ? (byName[n].stale ? "stale" : "live") : byName[n].error ?? "down"}`}>
                  <Dot ok={byName[n].ok} stale={byName[n].stale} />{byName[n].label}
                </a>
              ))}
              {venues.error && <span className="vd bad">server unreachable</span>}
            </div>
            <a href="#/risk/kill" className={`mode-pill ${tc?.kill_switch ? "pill-kill" : ""}`} data-help="Trading mode of the enabled venues and whether a strategy is running. Click to open the kill switch.">
              {pill}
            </a>
            <button className={`icon-btn help-toggle ${help ? "on" : ""}`} onClick={() => setHelp(!help)}
              aria-pressed={help} aria-label={help ? "Turn hover help off" : "Turn hover help on"}>?</button>
            <button className="icon-btn" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
          </div>
        </div>
        <main className="content">
          {found ? <Page route={route} venues={venues.data ?? []} health={health.data} />
            : <div className="empty"><h3>That page does not exist</h3><a href={`#/${DEFAULT_ROUTE}`}>Go to the summary</a></div>}
        </main>
      </div>
    </div>
  );
}
