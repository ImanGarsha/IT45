import { ReactNode, useMemo, useState } from "react";

// ---------- formatting -------------------------------------------------------
export const pct = (x: number | null | undefined, d = 2) =>
  x == null || !isFinite(x) ? "–" : `${(x * 100).toFixed(d)}%`;
export const bp = (x: number | null | undefined, d = 1) =>
  x == null || !isFinite(x) ? "–" : `${x.toFixed(d)} bp`;
export const usd = (x: number | null | undefined) => {
  if (x == null || !isFinite(x)) return "–";
  const a = Math.abs(x), s = x < 0 ? "-" : "";
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `${s}$${(a / 1e3).toFixed(1)}k`;
  if (a >= 100) return `${s}$${a.toFixed(0)}`;
  if (a >= 0.01 || a === 0) return `${s}$${a.toFixed(2)}`;
  return `${s}$${a.toFixed(4)}`;
};
export const price = (x: number | null | undefined) => {
  if (x == null || !isFinite(x)) return "–";
  const a = Math.abs(x);
  const d = a >= 1000 ? 1 : a >= 1 ? 3 : a >= 0.01 ? 5 : 8;
  return x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
};
export const ago = (s: number | null | undefined) =>
  s == null ? "never" : s < 60 ? `${s.toFixed(0)}s ago` : s < 3600 ? `${(s / 60).toFixed(0)}m ago`
    : `${(s / 3600).toFixed(1)}h ago`;
export const fee = (x: number | null | undefined) => (x == null ? "–" : `${(x * 1e4).toFixed(2)} bp`);

/** sign class: funding/edge colouring. Positive APR on a perp = longs pay. */
export const signCls = (x: number | null | undefined) =>
  x == null ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "";

// ---------- layout bits -------------------------------------------------------
export function PageHead({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {right && <div className="page-head-right">{right}</div>}
    </header>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "warn" | "err"; children: ReactNode }) {
  return <div className={`notice notice-${kind}`}>{children}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <div>{children}</div>}
    </div>
  );
}

export function Dot({ ok, stale }: { ok: boolean; stale?: boolean }) {
  return <span className={`dot ${ok && !stale ? "dot-ok" : ok ? "dot-stale" : "dot-bad"}`} />;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

// ---------- sortable table ----------------------------------------------------
export type Col<T> = {
  key: string; label: string; num?: boolean; width?: number | string;
  get?: (r: T) => number | string | null | undefined; render?: (r: T) => ReactNode; title?: string;
};

export function Table<T>({ rows, cols, initialSort, rowKey, onRow, empty, maxRows }:
  { rows: T[]; cols: Col<T>[]; initialSort?: [string, 1 | -1]; rowKey: (r: T, i: number) => string;
    onRow?: (r: T) => void; empty?: ReactNode; maxRows?: number }) {
  const [sort, setSort] = useState<[string, 1 | -1] | null>(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const c = cols.find((c) => c.key === sort[0]);
    if (!c) return rows;
    const g = c.get ?? ((r: T) => (r as Record<string, unknown>)[c.key] as number);
    return [...rows].sort((a, b) => {
      const x = g(a), y = g(b);
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sort[1];
    });
  }, [rows, cols, sort]);
  const shown = maxRows ? sorted.slice(0, maxRows) : sorted;
  if (!rows.length && empty) return <>{empty}</>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} className={c.num ? "num" : ""} style={{ width: c.width }} data-help={c.title}
                aria-sort={sort?.[0] === c.key ? (sort[1] === 1 ? "ascending" : "descending") : "none"}>
                <button onClick={() => setSort(sort?.[0] === c.key ? [c.key, (-sort[1]) as 1 | -1] : [c.key, c.num ? -1 : 1])}>
                  {c.label}
                  <span className="sort-ind">{sort?.[0] === c.key ? (sort[1] === 1 ? "▲" : "▼") : ""}</span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={rowKey(r, i)} onClick={onRow ? () => onRow(r) : undefined} className={onRow ? "clickable" : ""}>
              {cols.map((c) => (
                <td key={c.key} className={c.num ? "num" : ""}>
                  {c.render ? c.render(r) : String(c.get ? c.get(r) ?? "–" : (r as Record<string, unknown>)[c.key] ?? "–")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------- small line chart ---------------------------------------------------
const SERIES_VARS = ["--s1", "--s2", "--s3", "--s4", "--s5"];

export function LineChart({ series, height = 220, yFmt }:
  { series: { name: string; points: [number, number][] }[]; height?: number; yFmt: (v: number) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 760, H = height, P = { l: 64, r: 12, t: 12, b: 26 };
  const all = series.flatMap((s) => s.points);
  if (all.length < 2) return <Empty title="Not enough history yet">Snapshots are stored once a minute; the chart fills in as IT45 keeps running.</Empty>;
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys, 0), y1 = Math.max(...ys, 0);
  if (y0 === y1) { y0 -= 1e-9; y1 += 1e-9; }
  const sx = (x: number) => P.l + ((x - x0) / (x1 - x0 || 1)) * (W - P.l - P.r);
  const sy = (y: number) => P.t + (1 - (y - y0) / (y1 - y0)) * (H - P.t - P.b);
  const ticks = [y0, y0 + (y1 - y0) / 2, y1];
  const hx = hover == null ? null : x0 + ((hover - P.l) / (W - P.l - P.r)) * (x1 - x0);
  const nearest = (pts: [number, number][]) =>
    hx == null || !pts.length ? null : pts.reduce((a, b) => (Math.abs(b[0] - hx) < Math.abs(a[0] - hx) ? b : a));
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="funding history"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          setHover(px >= P.l && px <= W - P.r ? px : null);
        }} onMouseLeave={() => setHover(null)}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={P.l} x2={W - P.r} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={P.l - 8} y={sy(t) + 4} textAnchor="end" className="axis">{yFmt(t)}</text>
          </g>
        ))}
        <line x1={P.l} x2={W - P.r} y1={sy(0)} y2={sy(0)} className="zero" />
        <text x={P.l} y={H - 6} className="axis">{new Date(x0 * 1000).toLocaleString()}</text>
        <text x={W - P.r} y={H - 6} textAnchor="end" className="axis">{new Date(x1 * 1000).toLocaleString()}</text>
        {series.map((s, i) => (
          <polyline key={s.name} fill="none" strokeWidth={2} style={{ stroke: `var(${SERIES_VARS[i % 5]})` }}
            points={s.points.map((p) => `${sx(p[0])},${sy(p[1])}`).join(" ")} />
        ))}
        {hover != null && <line x1={hover} x2={hover} y1={P.t} y2={H - P.b} className="cursor" />}
      </svg>
      <div className="legend">
        {series.map((s, i) => {
          const n = nearest(s.points);
          return (
            <span key={s.name}>
              <i style={{ background: `var(${SERIES_VARS[i % 5]})` }} />{s.name}
              {n && <b> {yFmt(n[1])}</b>}
            </span>
          );
        })}
        {hx != null && <span className="muted">{new Date(hx * 1000).toLocaleTimeString()}</span>}
      </div>
    </div>
  );
}

// ---------- reconnect ----------------------------------------------------------
const RECONNECT_HELP = "Refresh every connection now without restarting IT45: new connections to all five venues' "
  + "price feeds with an immediate poll, the live order-book streams, and any testnet connector that failed to start. "
  + "Positions, pairs, orders and settings are not touched. If the internet itself is down it cannot help; "
  + "IT45 also retries by itself every few seconds.";

/** "Reconnect now" button plus a one-line result. */
export function ReconnectButton({ small }: { small?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch("/api/connections/reconnect", { method: "POST" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ?? r.status);
      const bad = Object.entries(j as Record<string, { feed: string; connector: string }>)
        .filter(([, v]) => v.feed !== "ok" || v.connector !== "ok").map(([k]) => k);
      const label = (v: string) => v[0].toUpperCase() + v.slice(1);
      setMsg(bad.length === Object.keys(j).length ? "Tried again: no venue answers. Check this computer's internet connection."
        : bad.length ? `Reconnected. Still not answering: ${bad.map(label).join(", ")}.` : "Reconnected: all venues answering.");
    } catch (e) {
      setMsg(`IT45 did not respond (${(e as Error).message}). Is it running?`);
    } finally { setBusy(false); }
  };
  return (
    <span className="cell-flex">
      <button type="button" className={small ? "btn-small" : "btn"} disabled={busy} onClick={go} data-help={RECONNECT_HELP}>
        {busy ? "Reconnecting" : "Reconnect now"}</button>
      {msg && <span className="small" data-help="Result of the last reconnect: venues that answered, and any that still did not.">{msg}</span>}
    </span>
  );
}
