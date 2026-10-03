import { ReactNode, useEffect, useState } from "react";
import { usePoll, VENUE_LABEL, VENUE_ORDER } from "../api";
import { ago, bp, Dot, Notice, PageHead, pct, price, signCls, Stat, Table, usd } from "../ui";

// ---------------------------------------------------------------- types
type StratCfg = {
  enabled: boolean; entry_edge_apr: number; exit_edge_apr: number; lookback_h: number;
  min_frac_positive: number; min_samples: number; max_breakeven_h: number; max_hold_h: number;
  max_divergence_bp: number; rotate: boolean; min_volume_usd: number; allow_rwa: boolean; eval_s: number;
};
export type TCfg = {
  kill_switch: boolean; venue_modes: Record<string, string>; paper_balance_usd: number; leverage: number;
  caps: { max_order_usd: number; max_pair_usd: number; max_venue_usd: number; max_gross_usd: number; max_pairs: number };
  brakes: { daily_loss_pct: number; imbalance_pct: number; imbalance_timeout_s: number; stale_s: number;
    min_liq_distance: number; max_api_errors: number };
  exec: { maker_attempts: number; reprice_s: number; taker_fallback: boolean; max_slippage_bp: number };
  strategies: Record<string, StratCfg>;
};
export type TStatus = {
  config: TCfg; halted: Record<string, string>; daily_loss_hit: boolean;
  risk: { day?: string; day_start_equity?: number; equity?: number; pnl_today?: number; limit?: number };
  broker_errors: Record<string, string>; brokers: Record<string, string>;
  strategies: Record<string, { enabled: boolean; note: string; last_eval: number }>;
  keys: Record<string, boolean>;
};
type Pair = { id: string; strategy: string; symbol: string; long_venue: string; long_kind: string;
  short_venue: string; target_usd: number; status: string; opened_ts: number; closed_ts: number | null;
  close_reason: string | null; entry_edge_apr: number | null; entry_basis_bp: number | null; notes: string };
type Pos = { venue: string; symbol: string; kind: string; size: number; avg_px: number; mark: number | null;
  upnl: number; realized: number; fees: number; funding: number; liq_px: number | null;
  liq_distance: number | null; notional: number };
type Account = { venue: string; mode: string; equity?: number; cash?: number; start?: number | null;
  margin_used?: number; pnl?: number; error?: string; resting_orders?: number };
type Order = { id: string; ts: number; venue: string; mode: string; symbol: string; kind: string; side: string;
  size: number; limit_px: number; post_only: number; reduce_only: number; status: string; filled: number;
  avg_px: number | null; fee: number; pair_id: string | null; error: string | null };
type Cand = { symbol: string; long_venue: string; short_venue: string; long_leg: string; edge_apr: number;
  cost_bp: number; breakeven_h: number | null; basis_bp: number | null; min_volume_usd: number;
  eligible: boolean; why_not?: string;
  persistence: { n: number; mean_edge_apr: number | null; frac_positive: number | null; hours_covered: number } };

const V = (n: string) => VENUE_LABEL[n] ?? n;
const STRAT_LABEL: Record<string, string> = { funding_arb: "Funding arb", spot_carry: "Spot carry" };

async function call(method: string, url: string, body?: unknown) {
  const r = await fetch(url, { method, headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `${r.status}`);
  return j;
}

export function useTrading(ms = 4000) {
  return usePoll<TStatus>("/api/trading/status", ms);
}

function useAction() {
  const [msg, setMsg] = useState<{ kind: "info" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (what: string, fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    try { await fn(); setMsg({ kind: "info", text: what }); after?.(); }
    catch (e) { setMsg({ kind: "err", text: `${what.replace(/\.$/, "")} failed: ${(e as Error).message}` }); }
    finally { setBusy(false); }
  };
  return { msg, busy, run, note: msg && <Notice kind={msg.kind}>{msg.text}</Notice> };
}

const saveCfg = (patch: unknown) => call("PUT", "/api/trading/config", patch);

/** Number field that edits in display units (e.g. percent) and saves in stored units. */
function NumField({ label, value, scale = 1, step = 1, min, hint, onChange }:
  { label: string; value: number; scale?: number; step?: number; min?: number; hint?: ReactNode;
    onChange: (v: number) => void }) {
  return (
    <label><span>{label}</span>
      <input type="number" step={step} min={min} value={+(value * scale).toFixed(6)}
        onChange={(e) => onChange(+e.target.value / scale)} />
      {hint && <small>{hint}</small>}
    </label>
  );
}

function Bool({ label, value, onChange, hint }: { label: string; value: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="switch-row"><input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}{hint && <small className="muted"> {hint}</small>}</span></label>
  );
}

function ModeBanner({ st }: { st: TStatus | null }) {
  if (!st) return null;
  if (st.config.kill_switch) return <Notice kind="err">Kill switch is ON: strategies are stopped and only closing orders pass. Release it under Risk, Kill switch.</Notice>;
  if (st.daily_loss_hit) return <Notice kind="err">Daily loss limit hit: only closing orders until 00:00 UTC.</Notice>;
  return null;
}

// ---------------------------------------------------------------- strategies
export function StrategyPage({ name }: { name: "funding_arb" | "spot_carry" }) {
  const t = useTrading();
  const c = usePoll<{ note: string; last_eval: number; rows: Cand[] }>(`/api/trading/candidates/${name}`, 15000);
  const pairs = usePoll<Pair[]>("/api/trading/pairs?include_closed=false", 5000);
  const a = useAction();
  const [f, setF] = useState<StratCfg | null>(null);
  useEffect(() => { if (t.data && !f) setF(t.data.config.strategies[name]); }, [t.data, f, name]);
  if (!t.data || !f) return <PageHead title={STRAT_LABEL[name]} sub="Loading" />;
  const live = t.data.config.strategies[name];
  const st = t.data.strategies[name];
  const set = (k: keyof StratCfg) => (v: number | boolean) => setF({ ...f, [k]: v });
  const mine = (pairs.data ?? []).filter((p) => p.strategy === name);
  const usedVenues = name === "spot_carry" ? ["hyperliquid"] : VENUE_ORDER;
  const modeVals = usedVenues.map((v) => t.data!.config.venue_modes[v]);
  const modes = modeVals.every((m) => m === modeVals[0]) ? `All ${modeVals[0]}`
    : usedVenues.map((v) => `${V(v)} ${t.data!.config.venue_modes[v]}`).join(", ");
  return (
    <>
      <PageHead title={STRAT_LABEL[name]}
        sub={name === "funding_arb"
          ? "Long the venue with the cheapest funding, short the richest, same asset and size. Opens only spreads that have persisted; closes on fade, age, price divergence or a better pair."
          : "Long Hyperliquid spot, short the Hyperliquid perp, while funding stays positive and has persisted."}
        right={<button className={`btn ${live.enabled ? "btn-stop" : ""}`} disabled={a.busy || t.data.config.kill_switch}
          onClick={() => a.run(live.enabled ? "Strategy stopped." : "Strategy started.",
            () => saveCfg({ strategies: { [name]: { enabled: !live.enabled } } }), t.reload)}>
          {live.enabled ? "Stop strategy" : "Start strategy"}</button>} />
      <ModeBanner st={t.data} />
      {a.note}
      <div className="stats">
        <Stat label="State" value={live.enabled ? "Running" : "Stopped"} hint={st.note || (st.last_eval ? `checked ${ago(Date.now() / 1000 - st.last_eval)}` : "not evaluated yet")} />
        <Stat label="Open pairs" value={mine.length} hint={`cap ${t.data.config.caps.max_pairs} across strategies`} />
        <Stat label="Venue modes" value={modes} hint="change under Risk, Limits" />
      </div>

      <section className="panel">
        <div className="panel-head"><h2>Open pairs</h2><a href="#/orders/positions">Positions</a></div>
        <PairsTable rows={mine} onChange={pairs.reload} />
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Candidates right now</h2><span className="muted small">{c.data?.note}</span></div>
        <p className="muted small">Spreads that clear the entry level, fee breakeven and volume filters. A row trades only when it is also eligible: its {f.lookback_h} h history must average above entry and be positive at least {Math.round(f.min_frac_positive * 100)}% of the time.</p>
        <Table rows={c.data?.rows ?? []} rowKey={(r) => `${r.symbol}-${r.long_venue}-${r.short_venue}`}
          empty={<div className="empty"><h3>No spread clears the entry filters</h3><div>Entry is {pct(f.entry_edge_apr, 0)} / yr with taker-fee breakeven under {f.max_breakeven_h} h.</div></div>}
          cols={[
            { key: "symbol", label: "Asset", render: (r) => <b>{r.symbol}</b> },
            { key: "long_venue", label: "Long", render: (r) => `${V(r.long_venue)}${r.long_leg === "spot" ? " spot" : ""}` },
            { key: "short_venue", label: "Short", render: (r) => V(r.short_venue) },
            { key: "edge_apr", label: "Now / yr", num: true, render: (r) => <span className={signCls(r.edge_apr)}>{pct(r.edge_apr, 1)}</span> },
            { key: "mean", label: `${f.lookback_h} h mean`, num: true, get: (r) => r.persistence.mean_edge_apr, render: (r) => pct(r.persistence.mean_edge_apr, 1) },
            { key: "pos", label: "Positive", num: true, get: (r) => r.persistence.frac_positive, render: (r) => pct(r.persistence.frac_positive, 0) },
            { key: "n", label: "Samples", num: true, get: (r) => r.persistence.n, render: (r) => r.persistence.n },
            { key: "breakeven_h", label: "Breakeven", num: true, render: (r) => r.breakeven_h == null ? "–" : `${r.breakeven_h.toFixed(1)} h` },
            { key: "eligible", label: "Trades?", get: (r) => (r.eligible ? 1 : 0), render: (r) => r.eligible ? <b className="pos">yes</b> : <span className="muted" title={r.why_not}>{r.why_not}</span> },
          ]} />
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Rules</h2></div>
        <form className="form" onSubmit={(e) => { e.preventDefault(); a.run("Rules saved.", () => saveCfg({ strategies: { [name]: { ...f, enabled: live.enabled } } }), t.reload); }}>
          <NumField label="Enter at funding spread (% / yr)" value={f.entry_edge_apr} scale={100} step={0.5} onChange={set("entry_edge_apr")} />
          <NumField label="Exit below (% / yr, 1 h average)" value={f.exit_edge_apr} scale={100} step={0.5} onChange={set("exit_edge_apr")} hint="Must be below the entry level, so a pair does not open and close on noise." />
          <NumField label="Persistence window (hours)" value={f.lookback_h} step={1} min={1} onChange={set("lookback_h")} />
          <NumField label="Positive at least (% of samples)" value={f.min_frac_positive} scale={100} step={5} onChange={set("min_frac_positive")} />
          <NumField label="Minimum samples (one per minute)" value={f.min_samples} step={10} min={1} onChange={set("min_samples")} />
          <NumField label="Taker-fee breakeven at most (hours)" value={f.max_breakeven_h} step={1} onChange={set("max_breakeven_h")} />
          <NumField label="Close after (hours held)" value={f.max_hold_h} step={12} onChange={set("max_hold_h")} />
          <NumField label="Close if leg prices diverge by (bp)" value={f.max_divergence_bp} step={10} onChange={set("max_divergence_bp")} />
          <NumField label="Minimum 24 h volume per leg (USD)" value={f.min_volume_usd} step={50000} onChange={set("min_volume_usd")} />
          <Bool label="Rotate into a better pair" hint="when the gain over 72 h beats the switching fees" value={f.rotate} onChange={set("rotate")} />
          <Bool label="Allow stocks, indices and metals" hint="off-hours rules and position fees are not modelled yet" value={f.allow_rwa} onChange={set("allow_rwa")} />
          <div><button type="submit" className="btn" disabled={a.busy}>Save rules</button></div>
        </form>
      </section>
    </>
  );
}

function PairsTable({ rows, onChange, closed }: { rows: Pair[]; onChange: () => void; closed?: boolean }) {
  const a = useAction();
  return (
    <>
      {a.note}
      <Table rows={rows} rowKey={(r) => r.id} initialSort={["opened_ts", -1]}
        empty={<div className="empty"><h3>{closed ? "No closed pairs yet" : "No open pairs"}</h3></div>}
        cols={[
          { key: "symbol", label: "Asset", render: (r) => <b>{r.symbol}</b> },
          { key: "strategy", label: "Strategy", render: (r) => STRAT_LABEL[r.strategy] ?? r.strategy },
          { key: "legs", label: "Long / short", get: (r) => r.long_venue, render: (r) => `${V(r.long_venue)}${r.long_kind === "spot" ? " spot" : ""} / ${V(r.short_venue)}` },
          { key: "target_usd", label: "Size per leg", num: true, render: (r) => usd(r.target_usd) },
          { key: "entry_edge_apr", label: "Entry spread", num: true, render: (r) => pct(r.entry_edge_apr, 1) },
          { key: "opened_ts", label: closed ? "Opened" : "Held", num: true, render: (r) => closed ? new Date(r.opened_ts * 1000).toLocaleString() : ago(Date.now() / 1000 - r.opened_ts).replace(" ago", "") },
          { key: "status", label: "Status", render: (r) => <span title={r.notes}>{r.status}</span> },
          ...(closed ? [{ key: "close_reason", label: "Why closed", render: (r: Pair) => <span className="wrap">{r.close_reason}</span> }]
            : [{ key: "act", label: "", render: (r: Pair) => r.status === "open" &&
              <button className="btn-small" disabled={a.busy} onClick={(e) => { e.stopPropagation(); a.run(`${r.symbol} pair closing.`, () => call("POST", `/api/trading/pairs/${r.id}/close`), onChange); }}>Close</button> }]),
        ]} />
    </>
  );
}

// ---------------------------------------------------------------- orders & positions
export function PositionsPage() {
  const t = useTrading();
  const pos = usePoll<Pos[]>("/api/trading/positions", 4000);
  const acc = usePoll<Account[]>("/api/trading/accounts", 5000);
  const pairs = usePoll<Pair[]>("/api/trading/pairs?include_closed=false", 5000);
  const a = useAction();
  const total = (acc.data ?? []).reduce((s, x) => s + (x.equity ?? 0), 0);
  const pnl = (acc.data ?? []).reduce((s, x) => s + (x.pnl ?? 0), 0);
  const fundingSum = (pos.data ?? []).reduce((s, p) => s + p.funding, 0);
  return (
    <>
      <PageHead title="Positions" sub="Every leg on every venue, the hedged pairs they belong to, and each venue's collateral. Paper accounts start at the balance set under Risk, Limits." />
      <ModeBanner st={t.data} />
      {a.note}
      <div className="stats">
        <Stat label="Equity, all venues" value={usd(total)} hint="paper balances plus testnet accounts" />
        <Stat label="Paper P&L since start" value={<span className={signCls(pnl)}>{usd(pnl)}</span>} hint="after fees and funding" />
        <Stat label="Funding on open legs" value={<span className={signCls(fundingSum)}>{usd(fundingSum)}</span>} hint="received minus paid" />
        <Stat label="Open pairs" value={pairs.data?.length ?? "–"} />
      </div>
      <section className="panel">
        <div className="panel-head"><h2>Pairs</h2></div>
        <PairsTable rows={pairs.data ?? []} onChange={pairs.reload} />
      </section>
      <section className="panel">
        <div className="panel-head"><h2>Legs</h2></div>
        <Table rows={pos.data ?? []} rowKey={(r) => `${r.venue}-${r.symbol}-${r.kind}`}
          empty={<div className="empty"><h3>No open legs</h3></div>} cols={[
            { key: "venue", label: "Venue", render: (r) => V(r.venue) },
            { key: "symbol", label: "Asset", render: (r) => <span><b>{r.symbol}</b> {r.kind === "spot" && <span className="tag">spot</span>}</span> },
            { key: "size", label: "Size", num: true, render: (r) => <span className={signCls(r.size)}>{r.size > 0 ? "+" : ""}{r.size.toPrecision(6)}</span> },
            { key: "notional", label: "Notional", num: true, render: (r) => usd(r.notional) },
            { key: "avg_px", label: "Entry", num: true, render: (r) => price(r.avg_px) },
            { key: "mark", label: "Mark", num: true, render: (r) => price(r.mark) },
            { key: "upnl", label: "Price P&L", num: true, render: (r) => <span className={signCls(r.upnl)}>{usd(r.upnl)}</span> },
            { key: "funding", label: "Funding", num: true, render: (r) => <span className={signCls(r.funding)}>{usd(r.funding)}</span> },
            { key: "fees", label: "Fees", num: true, render: (r) => usd(r.fees) },
            { key: "liq_distance", label: "To liquidation", num: true, render: (r) => r.liq_distance == null ? "–" : <span className={r.liq_distance < 0.3 ? "neg" : ""}>{pct(r.liq_distance, 0)}</span> },
          ]} />
      </section>
      <section className="panel">
        <div className="panel-head"><h2>Accounts</h2></div>
        <Table rows={acc.data ?? []} rowKey={(r) => r.venue} cols={[
          { key: "venue", label: "Venue", render: (r) => V(r.venue) },
          { key: "mode", label: "Mode" },
          { key: "equity", label: "Equity", num: true, render: (r) => usd(r.equity) },
          { key: "cash", label: "Cash", num: true, render: (r) => usd(r.cash) },
          { key: "margin_used", label: "Margin used", num: true, render: (r) => usd(r.margin_used) },
          { key: "pnl", label: "P&L", num: true, render: (r) => r.pnl == null ? "–" : <span className={signCls(r.pnl)}>{usd(r.pnl)}</span> },
          { key: "act", label: "", render: (r) => r.error ? <span className="err-text">{r.error}</span> : r.mode === "paper" &&
            <button className="btn-small" disabled={a.busy} onClick={() => a.run(`${V(r.venue)} paper account reset.`, () => call("POST", `/api/trading/venues/${r.venue}/reset-paper`), acc.reload)}>Reset paper</button> },
        ]} />
      </section>
    </>
  );
}

export function OpenOrdersPage() {
  const o = usePoll<Order[]>("/api/trading/orders?status=open", 3000);
  return (
    <>
      <PageHead title="Open orders" sub="Post-only orders resting on a venue. The executor re-prices or cancels them every few seconds; nothing rests for long." />
      <OrdersTable rows={o.data ?? []} />
    </>
  );
}

function OrdersTable({ rows }: { rows: Order[] }) {
  return (
    <Table rows={rows} rowKey={(r) => r.id} initialSort={["ts", -1]}
      empty={<div className="empty"><h3>No orders</h3></div>} cols={[
        { key: "ts", label: "Time", render: (r) => new Date(r.ts * 1000).toLocaleTimeString() },
        { key: "venue", label: "Venue", render: (r) => `${V(r.venue)} (${r.mode})` },
        { key: "symbol", label: "Asset", render: (r) => <span><b>{r.symbol}</b> {r.kind === "spot" && <span className="tag">spot</span>}</span> },
        { key: "side", label: "Side", render: (r) => <span className={r.side === "buy" ? "pos" : "neg"}>{r.side}</span> },
        { key: "type", label: "Type", get: (r) => (r.post_only ? "post-only" : "IOC") + (r.reduce_only ? ", reduce" : "") },
        { key: "size", label: "Size", num: true, render: (r) => r.size.toPrecision(6) },
        { key: "limit_px", label: "Limit", num: true, render: (r) => price(r.limit_px) },
        { key: "filled", label: "Filled", num: true, render: (r) => r.filled ? r.filled.toPrecision(6) : "–" },
        { key: "avg_px", label: "Avg price", num: true, render: (r) => price(r.avg_px) },
        { key: "fee", label: "Fee", num: true, render: (r) => r.fee ? usd(r.fee) : "–" },
        { key: "status", label: "Status", render: (r) => <span title={r.error ?? ""} className={r.status === "rejected" ? "neg" : ""}>{r.status}{r.error ? ": " + r.error : ""}</span> },
      ]} />
  );
}

export function HistoryPage() {
  const [tab, setTab] = useState<"pairs" | "orders" | "funding">("pairs");
  const pairs = usePoll<Pair[]>("/api/trading/pairs?include_closed=true", 8000);
  const orders = usePoll<Order[]>("/api/trading/orders?limit=500", 8000);
  const funding = usePoll<{ ts: number; venue: string; mode: string; symbol: string; amount: number }[]>("/api/trading/funding", 15000);
  return (
    <>
      <PageHead title="Trade history" sub="Closed pairs with the reason they closed, every order sent, and funding received or paid. Stored in data/it45.db."
        right={<div className="seg" role="group" aria-label="History">
          {(["pairs", "orders", "funding"] as const).map((x) => <button key={x} aria-pressed={tab === x} onClick={() => setTab(x)}>{x === "pairs" ? "Pairs" : x === "orders" ? "Orders" : "Funding"}</button>)}
        </div>} />
      {tab === "pairs" && <PairsTable rows={(pairs.data ?? []).filter((p) => !["opening", "open", "closing"].includes(p.status))} onChange={pairs.reload} closed />}
      {tab === "orders" && <OrdersTable rows={orders.data ?? []} />}
      {tab === "funding" && <Table rows={funding.data ?? []} rowKey={(r, i) => `${r.ts}-${i}`} initialSort={["ts", -1]}
        empty={<div className="empty"><h3>No funding yet</h3><div>Funding accrues on open perp legs and is booked once a minute.</div></div>} cols={[
          { key: "ts", label: "Time", render: (r) => new Date(r.ts * 1000).toLocaleString() },
          { key: "venue", label: "Venue", render: (r) => `${V(r.venue)} (${r.mode})` },
          { key: "symbol", label: "Asset" },
          { key: "amount", label: "Amount", num: true, render: (r) => <span className={signCls(r.amount)}>{usd(r.amount)}</span> },
        ]} />}
    </>
  );
}

// ---------------------------------------------------------------- risk
export function LimitsPage() {
  const t = useTrading();
  const a = useAction();
  const [f, setF] = useState<TCfg | null>(null);
  useEffect(() => { if (t.data && !f) setF(t.data.config); }, [t.data, f]);
  if (!f || !t.data) return <PageHead title="Limits" sub="Loading" />;
  const cap = (k: keyof TCfg["caps"]) => (v: number) => setF({ ...f, caps: { ...f.caps, [k]: v } });
  const ex = (k: keyof TCfg["exec"]) => (v: number | boolean) => setF({ ...f, exec: { ...f.exec, [k]: v } });
  const modeChange = (v: string, m: string) =>
    a.run(`${V(v)} set to ${m}.`, () => saveCfg({ venue_modes: { [v]: m } }), () => { t.reload(); setF(null); });
  return (
    <>
      <PageHead title="Limits" sub="Where IT45 may trade and how much. Paper fills are simulated on live prices; testnet uses the venue's test exchange with play money. Mainnet arrives in M3." />
      <ModeBanner st={t.data} />
      {a.note}
      <section className="panel">
        <div className="panel-head"><h2>Venue modes</h2></div>
        <table className="tbl compact">
          <thead><tr><th>Venue</th><th>Mode</th><th>Connector</th></tr></thead>
          <tbody>
            {VENUE_ORDER.map((v) => (
              <tr key={v}>
                <td>{V(v)}</td>
                <td>
                  <div className="seg" role="group" aria-label={`${V(v)} mode`}>
                    {["off", "paper", "testnet"].map((m) => (
                      <button key={m} aria-pressed={t.data!.config.venue_modes[v] === m} disabled={a.busy || (m === "testnet" && v !== "hyperliquid")}
                        title={m === "testnet" && v !== "hyperliquid" ? "No testnet connector for this venue yet" : undefined}
                        onClick={() => modeChange(v, m)}>{m}</button>
                    ))}
                  </div>
                </td>
                <td className="small">{t.data!.broker_errors[v] ? <span className="err-text">{t.data!.broker_errors[v]}</span>
                  : v === "hyperliquid" ? (t.data!.keys.hyperliquid_testnet ? "testnet key found in .env" : "paper; add a testnet key to .env for testnet")
                  : "paper only (real connector pending)"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">A venue with open positions cannot change mode. Close its pairs first.</p>
      </section>
      <form className="form" onSubmit={(e) => { e.preventDefault(); a.run("Limits saved.", () => saveCfg({ caps: f.caps, leverage: f.leverage, exec: f.exec, paper_balance_usd: f.paper_balance_usd }), () => { t.reload(); setF(null); }); }}>
        <h2 className="form-h">Size</h2>
        <NumField label="Per order (USD)" value={f.caps.max_order_usd} step={50} onChange={cap("max_order_usd")} />
        <NumField label="Per pair leg (USD)" value={f.caps.max_pair_usd} step={50} onChange={cap("max_pair_usd")} />
        <NumField label="Per venue, gross (USD)" value={f.caps.max_venue_usd} step={100} onChange={cap("max_venue_usd")} />
        <NumField label="Whole book, gross (USD)" value={f.caps.max_gross_usd} step={100} onChange={cap("max_gross_usd")} />
        <NumField label="Open pairs at once" value={f.caps.max_pairs} step={1} min={1} onChange={cap("max_pairs")} />
        <NumField label="Leverage per leg" value={f.leverage} step={0.5} min={1} onChange={(v) => setF({ ...f, leverage: v })} hint="Up to 10x. Funding arb gains nothing from leverage; it only moves liquidation closer." />
        <NumField label="Paper starting balance per venue (USD)" value={f.paper_balance_usd} step={100} onChange={(v) => setF({ ...f, paper_balance_usd: v })} hint="Applies when a paper account is reset." />
        <h2 className="form-h">Execution</h2>
        <NumField label="Post-only attempts before crossing" value={f.exec.maker_attempts} step={1} min={0} onChange={ex("maker_attempts")} />
        <NumField label="Re-price every (seconds)" value={f.exec.reprice_s} step={1} min={1} onChange={ex("reprice_s")} />
        <NumField label="Max distance for market orders (bp)" value={f.exec.max_slippage_bp} step={5} onChange={ex("max_slippage_bp")} />
        <Bool label="Cross the spread when post-only does not fill" value={f.exec.taker_fallback} onChange={ex("taker_fallback")} />
        <div><button type="submit" className="btn" disabled={a.busy}>Save limits</button></div>
      </form>
    </>
  );
}

export function BrakesPage() {
  const t = useTrading();
  const a = useAction();
  const [f, setF] = useState<TCfg["brakes"] | null>(null);
  useEffect(() => { if (t.data && !f) setF(t.data.config.brakes); }, [t.data, f]);
  if (!f || !t.data) return <PageHead title="Brakes" sub="Loading" />;
  const set = (k: keyof TCfg["brakes"]) => (v: number) => setF({ ...f, [k]: v });
  const r = t.data.risk;
  return (
    <>
      <PageHead title="Brakes" sub="Automatic stops that run every cycle, independent of the strategies." />
      <ModeBanner st={t.data} />
      {a.note}
      <div className="stats">
        <Stat label="P&L today (UTC)" value={<span className={signCls(r.pnl_today)}>{usd(r.pnl_today)}</span>} hint={`limit ${usd(r.limit)}`} />
        <Stat label="Halted venues" value={Object.keys(t.data.halted).length} hint={Object.keys(t.data.halted).map(V).join(", ") || "none"} />
        <Stat label="Daily loss brake" value={t.data.daily_loss_hit ? "Tripped" : "Armed"} />
      </div>
      {Object.entries(t.data.halted).map(([v, why]) => (
        <Notice key={v} kind="warn">
          <span className="cell-flex"><Dot ok={false} /> <b>{V(v)}</b> halted: {why}.</span>{" "}
          <button className="btn-small" onClick={() => a.run(`${V(v)} resumed.`, () => call("POST", `/api/trading/venues/${v}/resume`), t.reload)}>Resume {V(v)}</button>
        </Notice>
      ))}
      <form className="form" onSubmit={(e) => { e.preventDefault(); a.run("Brakes saved.", () => saveCfg({ brakes: f }), () => { t.reload(); setF(null); }); }}>
        <NumField label="Daily loss limit (% of starting capital)" value={f.daily_loss_pct} scale={100} step={0.5} onChange={set("daily_loss_pct")} hint="Hit: close everything, engage the kill switch, stop strategies." />
        <NumField label="Hedge imbalance tolerance (%)" value={f.imbalance_pct} scale={100} step={1} onChange={set("imbalance_pct")} hint="Long and short notional of a pair may differ by this much." />
        <NumField label="Lone-leg timeout (seconds)" value={f.imbalance_timeout_s} step={5} onChange={set("imbalance_timeout_s")} hint="After this, the heavier leg is trimmed or a lone leg closed." />
        <NumField label="Stale feed after (seconds)" value={f.stale_s} step={5} onChange={set("stale_s")} hint="No new orders on a venue whose prices are older than this." />
        <NumField label="Reduce when within (%) of liquidation" value={f.min_liq_distance} scale={100} step={5} onChange={set("min_liq_distance")} hint="The pair is halved." />
        <NumField label="Halt a venue after consecutive order errors" value={f.max_api_errors} step={1} min={1} onChange={set("max_api_errors")} />
        <div><button type="submit" className="btn" disabled={a.busy}>Save brakes</button></div>
      </form>
    </>
  );
}

export function KillPage() {
  const t = useTrading();
  const a = useAction();
  const [confirm, setConfirm] = useState(false);
  if (!t.data) return <PageHead title="Kill switch" sub="Loading" />;
  const on = t.data.config.kill_switch;
  return (
    <>
      <PageHead title="Kill switch" sub="Cancels every order and closes every position on every venue, stops both strategies, and then refuses everything except closing orders until released." />
      {a.note}
      <div className={`kill ${on ? "kill-on" : ""}`}>
        <div>
          <h2>{on ? "Kill switch is ON" : "Kill switch is off"}</h2>
          <p className="muted">{on ? "Nothing new can open. Release it to let the strategies be started again; they stay stopped until you start them." : "Use it when something looks wrong. Closing uses market orders, so it pays taker fees."}</p>
        </div>
        {on ? <button className="btn" disabled={a.busy} onClick={() => a.run("Kill switch released.", () => call("POST", "/api/trading/release"), t.reload)}>Release kill switch</button>
          : !confirm ? <button className="btn btn-stop btn-big" onClick={() => setConfirm(true)}>Close everything</button>
          : <div className="cell-flex">
              <button className="btn btn-stop btn-big" disabled={a.busy} onClick={() => a.run("Kill switch engaged: everything closed.", () => call("POST", "/api/trading/kill"), () => { setConfirm(false); t.reload(); })}>{a.busy ? "Closing" : "Yes, close everything now"}</button>
              <button className="btn-small" onClick={() => setConfirm(false)}>Cancel</button>
            </div>}
      </div>
    </>
  );
}

// ---------------------------------------------------------------- keys
export function KeysPage() {
  const t = useTrading();
  return (
    <>
      <PageHead title="API keys" sub="Keys are never typed into the dashboard. They live in a .env file in the IT45 folder that git ignores, and are read when IT45 starts." />
      <table className="tbl compact">
        <thead><tr><th>Venue</th><th>Key type</th><th>Can withdraw?</th><th>This build</th></tr></thead>
        <tbody>
          <tr><td>Hyperliquid</td><td>Agent (API) wallet approved by your account</td><td>No, by design of agent wallets</td>
            <td>{t.data?.keys.hyperliquid_testnet ? <b className="pos">testnet key found</b> : "testnet: add IT45_HL_AGENT_KEY and IT45_HL_ACCOUNT_ADDRESS"}</td></tr>
          <tr><td>Arcus</td><td>Ed25519 API key</td><td>Trade keys are refused for withdrawals unless you opt in</td><td>paper only</td></tr>
          <tr><td>Bulk</td><td>Ed25519 agent wallet</td><td>Not documented; confirm before funding</td><td>paper only</td></tr>
          <tr><td>Reya</td><td>EIP-712 signer</td><td>Not documented; confirm before funding</td><td>paper only</td></tr>
          <tr><td>Meridian</td><td>EVM linked signer</td><td>No (linked signers cannot withdraw)</td><td>paper only</td></tr>
        </tbody>
      </table>
      <p className="muted small">Setup steps for the Hyperliquid testnet agent key are in docs/03_keys.md. Restart IT45 after editing .env.</p>
    </>
  );
}
