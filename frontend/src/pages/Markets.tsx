import { useEffect, useMemo, useState } from "react";
import { MatrixRow, Quote, usePoll, VENUE_LABEL, VENUE_ORDER } from "../api";
import { bp, LineChart, Notice, PageHead, pct, price, signCls, Table, usd } from "../ui";

const hashQuery = () => decodeURIComponent(window.location.hash.split("?")[1] ?? "");

/** Diverging colour for a venue's rate RELATIVE to the asset's median across venues:
 * teal = richer than the others (the side to short), coral = cheaper (the side to long).
 * Colouring the absolute rate made every cell teal at the 10.95% base rate, hiding the spreads. */
function heat(apr: number | null | undefined, median: number | null): React.CSSProperties {
  if (apr == null || median == null) return {};
  const d = apr - median;
  if (Math.abs(d) < 0.005) return {};
  const a = Math.min(Math.abs(d) / 0.3, 1); // saturate at 30%/yr away from the median
  apr = d;
  const alpha = 0.08 + 0.62 * Math.sqrt(a);
  return { background: `rgb(var(${apr >= 0 ? "--pos-rgb" : "--neg-rgb"}) / ${alpha.toFixed(3)})` };
}

function median(r: MatrixRow): number | null {
  const xs = Object.values(r.venues).map((c) => c.funding_apr).filter((x): x is number => x != null).sort((a, b) => a - b);
  if (!xs.length) return null;
  const m = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[m] : (xs[m - 1] + xs[m]) / 2;
}

function useMatrix() {
  return usePoll<MatrixRow[]>("/api/matrix", 5000);
}

function History({ symbol }: { symbol: string }) {
  const [hours, setHours] = useState(24);
  const h = usePoll<{ ts: number; venue: string; funding_1h: number | null }[]>(
    `/api/history/${encodeURIComponent(symbol)}?hours=${hours}`, 60000);
  const series = useMemo(() => {
    const by: Record<string, [number, number][]> = {};
    for (const r of h.data ?? []) if (r.funding_1h != null) (by[r.venue] ??= []).push([r.ts, r.funding_1h * 8760]);
    return VENUE_ORDER.filter((v) => by[v]).map((v) => ({ name: VENUE_LABEL[v], points: by[v] }));
  }, [h.data]);
  return (
    <section className="panel">
      <div className="panel-head">
        <h2 data-help="Funding of this asset on each venue over time, annualised, from the stored snapshots. Hover the chart to read values at a moment.">{symbol} funding history, annualised</h2>
        <div className="seg" role="group" aria-label="History window">
          {[6, 24, 72, 168].map((x) => <button key={x} aria-pressed={hours === x} onClick={() => setHours(x)}>{x < 168 ? `${x}h` : "7d"}</button>)}
        </div>
      </div>
      <LineChart series={series} yFmt={(v) => pct(v, 1)} />
    </section>
  );
}

export function FundingMatrix() {
  const m = useMatrix();
  const [sym, setSym] = useState(hashQuery());
  const [filter, setFilter] = useState("");
  const [cat, setCat] = useState("all");
  useEffect(() => { const on = () => setSym(hashQuery()); window.addEventListener("hashchange", on); return () => window.removeEventListener("hashchange", on); }, []);
  const rows = (m.data ?? []).filter((r) => (cat === "all" || (cat === "crypto" ? r.category === "crypto" : r.category !== "crypto"))
    && r.symbol.toLowerCase().includes(filter.toLowerCase()));
  const pick = (s: string) => { window.location.hash = `#/markets/funding?${s}`; };
  return (
    <>
      <PageHead title="Funding matrix" sub="Current funding per venue, annualised (positive: longs pay shorts). Shading compares each venue with the asset's other venues: teal is richer, the side to short; coral is cheaper, the side to long. Click a row for its history."
        right={<div className="controls">
          <input type="search" placeholder="Filter assets" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter assets" />
          <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Asset class">
            <option value="all">All assets</option><option value="crypto">Crypto</option><option value="rwa">Stocks, indices, metals</option>
          </select>
        </div>} />
      {m.error && <Notice kind="err">{m.error}</Notice>}
      {sym && <History symbol={sym} />}
      <div className="table-wrap">
        <table className="tbl heat">
          <thead>
            <tr>
              <th>Asset</th>
              {VENUE_ORDER.map((v) => <th key={v} className="num">{VENUE_LABEL[v]}</th>)}
              <th className="num">Widest spread</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className={`clickable ${sym === r.symbol ? "selected" : ""}`} onClick={() => pick(r.symbol)}>
                {/* median of listed venues, for the relative colouring */}
                <td><b>{r.symbol}</b> {r.category !== "crypto" && <span className="tag">{r.category}</span>}</td>
                {VENUE_ORDER.map((v) => {
                  const c = r.venues[v];
                  return <td key={v} className="num hc" style={heat(c?.funding_apr, median(r))}
                    data-help={c ? `${c.raw_symbol}: ${pct(c.funding_apr, 2)} / yr, 24h vol ${usd(c.volume_24h_usd)}` : "not listed"}>
                    {c ? pct(c.funding_apr, 1) : ""}</td>;
                })}
                <td className="num"><b>{pct(r.spread_apr, 1)}</b></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PriceMatrix() {
  const m = useMatrix();
  return (
    <>
      <PageHead title="Price matrix" sub="Mid price per venue (mark where a venue has no book mid). Dispersion is the spread between the highest and lowest venue." />
      {m.error && <Notice kind="err">{m.error}</Notice>}
      <Table rows={m.data ?? []} rowKey={(r) => r.symbol} initialSort={["n", -1]} cols={[
        { key: "symbol", label: "Asset", render: (r) => <b>{r.symbol}</b> },
        { key: "n", label: "Venues", num: true, get: (r) => Object.keys(r.venues).length },
        ...VENUE_ORDER.map((v) => ({ key: v, label: VENUE_LABEL[v], num: true,
          get: (r: MatrixRow) => r.venues[v]?.px ?? null, render: (r: MatrixRow) => price(r.venues[v]?.px) })),
        { key: "px_dispersion_bp", label: "Dispersion", num: true, render: (r) => bp(r.px_dispersion_bp) },
      ]} />
    </>
  );
}

export function SpotList() {
  const q = usePoll<Quote[]>("/api/venues/hyperliquid/markets", 6000);
  const spot = (q.data ?? []).filter((x) => x.kind === "spot");
  const perp = Object.fromEntries((q.data ?? []).filter((x) => x.kind === "perp").map((x) => [x.symbol, x]));
  return (
    <>
      <PageHead title="Hyperliquid spot" sub="USDC-quoted spot pairs. Where a perp exists for the same asset, its funding is shown: that is the carry a spot-long, perp-short pair earns." />
      <Table rows={spot} rowKey={(r) => r.raw_symbol} initialSort={["volume_24h_usd", -1]} cols={[
        { key: "symbol", label: "Asset", render: (r) => <b>{r.symbol}</b> },
        { key: "raw_symbol", label: "Pair id" },
        { key: "px", label: "Mid", num: true, render: (r) => price(r.px) },
        { key: "volume_24h_usd", label: "24h volume", num: true, render: (r) => usd(r.volume_24h_usd) },
        { key: "perp", label: "Perp funding / yr", num: true, get: (r) => perp[r.symbol]?.funding_apr ?? null,
          render: (r) => perp[r.symbol] ? <span className={signCls(perp[r.symbol].funding_apr)}>{pct(perp[r.symbol].funding_apr)}</span> : "no perp" },
        { key: "gap", label: "Perp vs spot", num: true,
          get: (r) => perp[r.symbol]?.px && r.px ? (perp[r.symbol].px! - r.px) / r.px * 1e4 : null,
          render: (r) => perp[r.symbol]?.px && r.px ? bp((perp[r.symbol].px! - r.px) / r.px * 1e4) : "–" },
      ]} />
    </>
  );
}
