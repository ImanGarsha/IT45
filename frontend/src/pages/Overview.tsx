import { OppResp, usePoll } from "../api";
import { ago, Dot, PageHead, pct, Stat, Table, bp, Notice } from "../ui";
import type { PageProps } from ".";
import { oppCols } from "./Opps";

export function Summary({ venues, health }: PageProps) {
  const opps = usePoll<OppResp>("/api/opportunities?limit=8", 6000);
  const top = opps.data?.rows[0];
  const perps = venues.reduce((a, v) => a + v.perps, 0);
  return (
    <>
      <PageHead title="Summary" sub="Live funding and prices from all five venues. Nothing trades in this build." />
      <div className="stats">
        <Stat label="Venue feeds live" value={`${health?.venues_ok ?? "–"} / ${health?.venues_enabled ?? 5}`}
          hint={venues.filter((v) => !v.ok).map((v) => v.label).join(", ") || "all responding"} />
        <Stat label="Perp markets tracked" value={perps || "–"} hint={`${venues.reduce((a, v) => a + v.spot, 0)} Hyperliquid spot pairs`} />
        <Stat label="Best net funding spread" value={top ? pct(top.net_apr, 1) : "–"}
          hint={top ? `${top.symbol}: long ${top.long_venue}, short ${top.short_venue}` : "waiting for data"} />
        <Stat label="Stored snapshots" value={health?.snapshot_rows?.toLocaleString() ?? "–"}
          hint={health?.since ? `since ${new Date(health.since * 1000).toLocaleString()}` : "one per minute per market"} />
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Venues</h2>
          <a href="#/overview/health">Details</a>
        </div>
        <div className="venue-grid">
          {venues.map((v) => (
            <a key={v.name} className="venue-card" href={`#/venues/${v.name}`}>
              <div className="vc-top"><Dot ok={v.ok} stale={v.stale} /><b>{v.label}</b></div>
              <div className="vc-row"><span>Markets</span><span className="num">{v.perps}{v.spot ? ` + ${v.spot} spot` : ""}</span></div>
              <div className="vc-row"><span>Updated</span><span className="num">{ago(v.age_s)}</span></div>
              <div className="vc-row"><span>Fees m/t</span><span className="num">{bp(v.maker_fee * 1e4, 2)} / {bp(v.taker_fee * 1e4, 2)}</span></div>
              {v.error && <div className="vc-err">{v.error}</div>}
            </a>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Top cross-venue funding spreads</h2>
          <a href="#/opportunities/cross">All opportunities</a>
        </div>
        {opps.data && <p className="muted small">Net of {opps.data.mode} fees over {opps.data.horizon_hours} h, if today's rates held. Rates move hourly; this is a screen, not a forecast.</p>}
        {opps.error && <Notice kind="err">{opps.error}</Notice>}
        <Table rows={opps.data?.rows ?? []} cols={oppCols.filter((c) => !["flags", "basis"].includes(c.key))}
          rowKey={(r) => `${r.symbol}-${r.long_venue}-${r.short_venue}`} />
      </section>
    </>
  );
}

export function Health({ venues }: PageProps) {
  return (
    <>
      <PageHead title="Venue health" sub="Each venue is polled on its public API. A feed older than 30 s is marked stale and excluded from opportunities." />
      <Table rows={venues} rowKey={(v) => v.name} cols={[
        { key: "label", label: "Venue", render: (v) => <span className="cell-flex"><Dot ok={v.ok} stale={v.stale} /><a href={`#/venues/${v.name}`}>{v.label}</a></span> },
        { key: "state", label: "State", get: (v) => (!v.enabled ? "disabled" : v.ok ? (v.stale ? "stale" : "live") : "down") },
        { key: "perps", label: "Perps", num: true },
        { key: "spot", label: "Spot", num: true },
        { key: "latency_ms", label: "Latency", num: true, render: (v) => v.latency_ms == null ? "–" : `${v.latency_ms.toFixed(0)} ms` },
        { key: "age_s", label: "Last update", num: true, render: (v) => ago(v.age_s) },
        { key: "fails", label: "Failed polls in a row", num: true },
        { key: "error", label: "Last error", render: (v) => <span className="err-text">{v.error ?? ""}</span> },
      ]} />
    </>
  );
}
