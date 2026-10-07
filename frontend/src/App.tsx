import { useEffect, useState } from "react";
import { Health, usePoll, VenueStatus, VENUE_ORDER } from "./api";
import { HelpLayer, useHelpEnabled } from "./HelpLayer";
import { Icon } from "./icons";
import { DEFAULT_ROUTE, findRoute, NAV } from "./nav";
import { Page } from "./pages";
import { useTrading } from "./pages/Trading";
import { Dot, ReconnectButton } from "./ui";

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

/** Says plainly what an outage means for trading: what is paused, what is not, what resumes by itself. */
function OfflineBanner({ serverDown, venues, modes, unknown }:
  { serverDown: boolean; venues: VenueStatus[]; modes?: Record<string, string>; unknown: number }) {
  if (serverDown) {
    return <div className="offline bad" role="alert" data-help="The dashboard gets no answer from IT45 on this computer. IT45 has stopped, or this browser lost its connection to it. Nothing trades while IT45 is stopped.">
      <b>IT45 is not responding.</b> It has stopped or this page lost its connection. Nothing is trading;
      open positions stay on the venues. Start it again with <code>.\run.ps1</code>.</div>;
  }
  const traded = venues.filter((v) => v.enabled && modes && modes[v.name] && modes[v.name] !== "off");
  const down = traded.filter((v) => !v.ok || v.stale);
  const notes = [];
  if (traded.length && down.length === traded.length) {
    notes.push(<div key="all" className="offline bad" role="alert" data-help="No venue you trade on has answered within the stale limit. Usually the internet connection is down or the computer slept. The guard refuses new orders on stale prices. Brakes, strategy exits and the daily-loss check leave positions alone while a venue is unreachable and for 60 s after it returns, so an outage is never read as a loss or a missing leg.">
      <b>No venue is answering: the internet connection may be down.</b> New orders are paused. Open positions
      stay open: a disconnection never engages the kill switch or closes anything. Brakes resume 60 s after the connection is back.
      <div className="offline-act"><ReconnectButton small /></div></div>);
  } else if (down.length) {
    notes.push(<div key="some" className="offline warn" data-help="These venues have not answered within the stale limit. No new orders go there until their prices are fresh again; other venues trade normally.">
      <b>{down.map((v) => v.label).join(", ")} not answering.</b> No new orders there until prices are fresh again; its positions stay open.
      <div className="offline-act"><ReconnectButton small /></div></div>);
  }
  if (unknown > 0) {
    notes.push(<div key="unk" className="offline warn" data-help="Orders that were sent while the connection dropped, so no answer came back. IT45 asks the venue what became of each one once it answers again: a resting order is cancelled, a fill is booked, and the brakes repair any pair.">
      <b>{unknown} order{unknown > 1 ? "s" : ""} sent without a reply.</b> IT45 will ask the venue what became of {unknown > 1 ? "them" : "it"} and cancel anything left resting.</div>);
  }
  return notes.length ? <>{notes}</> : null;
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
        <OfflineBanner serverDown={!!venues.error} venues={venues.data ?? []}
          modes={tc?.venue_modes} unknown={trading.data?.unknown_orders ?? 0} />
        <main className="content">
          {found ? <Page route={route} venues={venues.data ?? []} health={health.data} />
            : <div className="empty"><h3>That page does not exist</h3><a href={`#/${DEFAULT_ROUTE}`}>Go to the summary</a></div>}
        </main>
      </div>
    </div>
  );
}
