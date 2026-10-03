import { useState } from "react";
import { Opp, OppResp, usePoll, VENUE_LABEL } from "../api";
import { bp, Col, Notice, PageHead, pct, signCls, Table, usd } from "../ui";

const V = (n: string) => VENUE_LABEL[n] ?? n;

export const oppCols: Col<Opp>[] = [
  { key: "symbol", label: "Asset", render: (o) => <a href={`#/markets/funding?${o.symbol}`}><b>{o.symbol}</b></a> },
  { key: "long_venue", label: "Long", render: (o) => <span>{V(o.long_venue)}{o.long_leg === "spot" ? " spot" : ""} <span className={`muted ${signCls(-o.long_funding_apr)}`}>{pct(o.long_funding_apr, 1)}</span></span> },
  { key: "short_venue", label: "Short", render: (o) => <span>{V(o.short_venue)} <span className={`muted ${signCls(o.short_funding_apr)}`}>{pct(o.short_funding_apr, 1)}</span></span> },
  { key: "edge_apr", label: "Gross spread / yr", num: true, render: (o) => <span className={signCls(o.edge_apr)}>{pct(o.edge_apr)}</span>,
    title: "Short leg funding minus long leg funding, annualised" },
  { key: "cost_bp", label: "Round-trip fees", num: true, render: (o) => bp(o.cost_bp),
    title: "Open and close on both legs" },
  { key: "net_apr", label: "Net / yr", num: true, render: (o) => <b className={signCls(o.net_apr)}>{pct(o.net_apr)}</b>,
    title: "Over the horizon, after fees, if rates stayed where they are" },
  { key: "breakeven_h", label: "Breakeven", num: true, render: (o) => o.breakeven_h == null ? "–" : o.cost_bp < 1e-9 ? "no fees" : `${o.breakeven_h.toFixed(1)} h`,
    title: "Hours of today's spread needed to pay the fees" },
  { key: "basis", label: "Price gap", num: true, get: (o) => o.basis_bp, render: (o) => bp(o.basis_bp),
    title: "Short price minus long price. Positive = you sell the richer leg" },
  { key: "persist", label: "6 h mean", num: true, get: (o) => o.persistence?.mean_edge_apr ?? null,
    title: "Average funding spread over the last 6 hours from stored snapshots, and the share of minutes it was positive",
    render: (o) => o.persistence && o.persistence.n ? <span data-help={`${o.persistence.n} samples, positive ${pct(o.persistence.frac_positive, 0)} of the time`}>{pct(o.persistence.mean_edge_apr, 1)} <span className="muted">{pct(o.persistence.frac_positive, 0)}</span></span> : <span className="muted">no history</span> },
  { key: "min_volume_usd", label: "Thinner leg 24h vol", num: true, render: (o) => usd(o.min_volume_usd) },
  { key: "flags", label: "Check", get: (o) => o.flags.length, render: (o) => o.flags.length
      ? <span className="flags" data-help={o.flags.join(". ")}>{o.flags.length} note{o.flags.length > 1 ? "s" : ""}</span> : "" },
];

function Controls({ q, set }: { q: { h: number; m: string; v: number }; set: (x: { h: number; m: string; v: number }) => void }) {
  return (
    <div className="controls">
      <label>Hold for
        <select value={q.h} onChange={(e) => set({ ...q, h: +e.target.value })}>
          {[8, 24, 72, 168, 720].map((h) => <option key={h} value={h}>{h < 168 ? `${h} h` : h === 168 ? "1 week" : "30 days"}</option>)}
        </select>
      </label>
      <label>Fees
        <select value={q.m} onChange={(e) => set({ ...q, m: e.target.value })}>
          <option value="maker">Maker (post-only)</option>
          <option value="taker">Taker (market)</option>
        </select>
      </label>
      <label>Min 24h volume per leg
        <select value={q.v} onChange={(e) => set({ ...q, v: +e.target.value })}>
          {[0, 50_000, 100_000, 500_000, 1_000_000, 10_000_000].map((v) => <option key={v} value={v}>{v ? usd(v) : "any"}</option>)}
        </select>
      </label>
    </div>
  );
}

function OppPage({ endpoint, title, sub, empty }: { endpoint: string; title: string; sub: string; empty: string }) {
  const [q, setQ] = useState({ h: 72, m: "maker", v: 100_000 });
  const [sel, setSel] = useState<Opp | null>(null);
  const r = usePoll<OppResp>(`${endpoint}?horizon=${q.h}&mode=${q.m}&min_volume=${q.v}`, 5000);
  return (
    <>
      <PageHead title={title} sub={sub} right={<Controls q={q} set={setQ} />} />
      <Notice>
        Rates are today's and change every hour. The 6 h column shows how the spread has actually behaved; the strategies
        only trade spreads that have persisted (rules under Strategies).
      </Notice>
      {r.error && <Notice kind="err">{r.error}</Notice>}
      <Table rows={r.data?.rows ?? []} cols={oppCols} initialSort={["net_apr", -1]} onRow={setSel}
        rowKey={(o) => `${o.symbol}-${o.long_venue}-${o.long_leg}-${o.short_venue}`}
        empty={r.data ? <div className="empty"><h3>No spreads pass the filters</h3><div>Lower the volume floor or pick a longer holding period.</div></div> : <div className="empty"><h3>Loading</h3></div>} />
      {sel && (
        <aside className="drawer" aria-label="Opportunity details">
          <button className="drawer-close" onClick={() => setSel(null)} aria-label="Close details">×</button>
          <h2>{sel.symbol}: long {V(sel.long_venue)}{sel.long_leg === "spot" ? " spot" : ""}, short {V(sel.short_venue)}</h2>
          <dl className="kv">
            <dt>Long leg funding</dt><dd>{pct(sel.long_funding_apr)} / yr {sel.long_funding_apr > 0 ? "(you pay)" : sel.long_funding_apr < 0 ? "(you receive)" : ""}</dd>
            <dt>Short leg funding</dt><dd>{pct(sel.short_funding_apr)} / yr {sel.short_funding_apr > 0 ? "(you receive)" : sel.short_funding_apr < 0 ? "(you pay)" : ""}</dd>
            <dt>Gross spread</dt><dd>{pct(sel.edge_apr)} / yr</dd>
            <dt>Fees, open + close</dt><dd>{bp(sel.cost_bp)}</dd>
            <dt>Net over {r.data?.horizon_hours} h</dt><dd>{bp(sel.net_bp)} of notional ({pct(sel.net_apr)} / yr)</dd>
            <dt>Price gap at entry</dt><dd>{bp(sel.basis_bp)}</dd>
          </dl>
          {sel.flags.length > 0 && <><h3>Check before trading</h3><ul>{sel.flags.map((f) => <li key={f}>{f}</li>)}</ul></>}
          <a href={`#/markets/funding?${sel.symbol}`}>Funding history for {sel.symbol}</a>
        </aside>
      )}
      {!r.error && r.data && <p className="muted small">{empty}</p>}
    </>
  );
}

export const CrossOpps = () => (
  <OppPage endpoint="/api/opportunities" title="Cross-venue funding"
    sub="Same asset, two venues: long where funding is cheapest, short where it is richest. Price moves cancel; the funding difference is the carry."
    empty="Every pair of venues listing the same asset is compared; only the profitable direction is shown." />
);

export const CarryOpps = () => (
  <OppPage endpoint="/api/carry" title="Spot-perp carry (Hyperliquid)"
    sub="Buy spot and short the perp on Hyperliquid. You collect funding while it stays positive."
    empty="Only assets with positive perp funding are listed; a negative rate would need a spot short, which v1 does not do." />
);
