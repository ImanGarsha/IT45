import { useCallback, useEffect, useMemo, useState } from "react";
import { Quote, usePoll, VENUE_LABEL, VENUE_ORDER } from "../api";
import { bp, Notice, PageHead, pct, price, signCls, usd } from "../ui";
import { TStatus, useTrading } from "./Trading";

type Ticket = {
  venue: string; symbol: string; kind: string; mode: string;
  book: { bid: number; ask: number; mid: number; age_s: number } | null;
  position: { size: number; avg_px: number; notional: number; upnl: number; funding: number } | null;
  account: { equity?: number; cash?: number; margin_used?: number; error?: string };
  maker_fee: number | null; taker_fee: number | null; funding_apr: number | null; max_leverage: number | null;
  caps: { max_order_usd: number }; leverage: number;
};

const V = (n: string) => VENUE_LABEL[n] ?? n;

async function post(url: string, body: unknown) {
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `${r.status}`);
  return j;
}

const compact = (x: number | null | undefined) => x == null ? "–" :
  x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : x >= 1e3 ? `$${(x / 1e3).toFixed(0)}k` : `$${x.toFixed(0)}`;

/** Every tradable market of a kind on a venue, busiest first. */
function useMarkets(venue: string, kind: string) {
  const m = usePoll<Quote[]>(venue ? `/api/venues/${venue}/markets` : null, 30000);
  return useMemo(() => {
    const seen = new Set<string>();     // one row per symbol (the busiest), so the choice is unambiguous
    return (m.data ?? []).filter((q) => q.kind === kind && q.status === "trading")
      .sort((a, b) => (b.volume_24h_usd ?? 0) - (a.volume_24h_usd ?? 0))
      .filter((q) => !seen.has(q.symbol) && !!seen.add(q.symbol));
  }, [m.data, kind]);
}

/** Dropdown of markets with a search box that narrows it. Keeps the choice valid when the list changes. */
function MarketPicker({ label, markets, value, onChange }:
  { label: string; markets: Quote[]; value: string; onChange: (s: string) => void }) {
  const [q, setQ] = useState("");
  useEffect(() => {
    if (markets.length && !markets.some((m) => m.symbol === value)) onChange(markets[0].symbol);
  }, [markets, value, onChange]);
  const shown = markets.filter((m) => !q || m.symbol.includes(q.toUpperCase()) || m.symbol === value);
  return (
    <label data-help={`Pick the market to trade. ${markets.length} tradable here, busiest first; type in the search box to narrow the list.`}>
      <span>{label} <span className="muted small">({markets.length})</span></span>
      <span className="amount">
        <input type="search" placeholder="Search" aria-label="Search markets" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 100 }} />
        <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
          {shown.map((m) => <option key={m.symbol} value={m.symbol}>{m.symbol}  {price(m.mid ?? m.px)}  vol {compact(m.volume_24h_usd)}</option>)}
        </select>
      </span>
    </label>
  );
}

function liveVenues(st: TStatus | null) {
  return VENUE_ORDER.filter((v) => st && st.config.venue_modes[v] !== "off");
}

// ------------------------------------------------------------------ single order
function OrderTicket({ st }: { st: TStatus | null }) {
  const venues = liveVenues(st);
  const [venue, setVenue] = useState("");
  const [symbol, setSymbol] = useState("BTC");
  const [kind, setKind] = useState<"perp" | "spot">("perp");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<"market" | "limit" | "post_only">("market");
  const [amount, setAmount] = useState("100");
  const [unit, setUnit] = useState<"usd" | "units">("usd");
  const [px, setPx] = useState("");
  const [reduce, setReduce] = useState(false);
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "info" | "err"; text: string } | null>(null);
  useEffect(() => { if (!venue && venues.length) setVenue(venues[0]); }, [venues, venue]);
  useEffect(() => { if (venue !== "hyperliquid") setKind("perp"); }, [venue]);

  const markets = useMarkets(venue, kind);
  const pick = useCallback((s: string) => { setSymbol(s); setReview(false); }, []);
  const tk = usePoll<Ticket>(venue && symbol ? `/api/trading/ticket?venue=${venue}&symbol=${encodeURIComponent(symbol)}&kind=${kind}` : null, 2000);
  const t = tk.data;
  const mid = t?.book?.mid ?? 0;
  const qty = unit === "usd" ? (mid ? +amount / mid : 0) : +amount;
  const limit = +px;
  const refPx = type === "market" ? (side === "buy" ? t?.book?.ask : t?.book?.bid) ?? mid : limit || mid;
  const notional = qty * (refPx || 0);
  const crosses = type !== "market" && t?.book && limit > 0 && (side === "buy" ? limit >= t.book.ask : limit <= t.book.bid);
  const taker = type === "market" || !!crosses;
  const feeRate = (taker ? t?.taker_fee : t?.maker_fee) ?? 0;
  const margin = kind === "spot" ? notional : notional / (t?.leverage || 1);
  const overCap = t ? notional > t.caps.max_order_usd : false;
  const problems = [
    !venue && "pick a venue",
    !t?.book && "no live book for this market",
    !(qty > 0) && "enter a size",
    type !== "market" && !(limit > 0) && "enter a limit price",
    type === "post_only" && crosses && "a post-only order at this price would cross the book",
    overCap && `notional ${usd(notional)} is above the per-order cap ${usd(t!.caps.max_order_usd)}`,
    st?.config.kill_switch && !reduce && "kill switch is on: only reduce-only orders pass",
  ].filter(Boolean) as string[];

  const fill = (which: "bid" | "mid" | "ask") => t?.book && setPx(String(+t.book[which].toPrecision(8)));
  const place = async () => {
    setBusy(true);
    try {
      const r = await post("/api/trading/manual/order", { venue, symbol, kind, side, type, reduce_only: reduce,
        ...(unit === "usd" ? { usd: +amount } : { size: +amount }), ...(type === "market" ? {} : { price: limit }) });
      const ok = !r.error || r.status === "open" || r.status === "filled";
      setResult({ kind: ok && r.status !== "refused" && r.status !== "not filled" ? "info" : "err",
        text: r.status === "filled" ? `Filled ${(+r.filled).toPrecision(6)} ${symbol} at ${price(r.avg_px)}, fee ${usd(r.fees)}.`
          : r.status === "open" ? `Order resting on ${V(venue)} as maker. See Open orders to cancel it.`
          : `${r.status}: ${r.error ?? ""}` });
      setReview(false);
      tk.reload();
    } catch (e) {
      setResult({ kind: "err", text: `Order not placed: ${(e as Error).message}` });
    } finally { setBusy(false); }
  };

  return (
    <section className="panel">
      <div className="panel-head"><h2>Single order</h2></div>
      <div className="ticket">
        <form className="form ticket-form" onSubmit={(e) => { e.preventDefault(); if (!problems.length) setReview(true); }}>
          <label><span>Venue</span>
            <select value={venue} onChange={(e) => { setVenue(e.target.value); setReview(false); }}>
              {venues.map((v) => <option key={v} value={v}>{V(v)} ({st!.config.venue_modes[v]})</option>)}
            </select></label>
          {venue === "hyperliquid" && (
            <div className="field-row"><span>Market type</span>
              <div className="seg" role="group" aria-label="Market type">
                <button type="button" aria-pressed={kind === "perp"} onClick={() => setKind("perp")}>Perp</button>
                <button type="button" aria-pressed={kind === "spot"} onClick={() => setKind("spot")}>Spot</button>
              </div></div>)}
          <MarketPicker key={venue + kind} label="Market" markets={markets} value={symbol} onChange={pick} />
          <div className="field-row"><span>Side</span>
            <div className="seg seg-side" role="group" aria-label="Side">
              <button type="button" className="buy" aria-pressed={side === "buy"} onClick={() => { setSide("buy"); setReview(false); }}>Buy / long</button>
              <button type="button" className="sell" aria-pressed={side === "sell"} onClick={() => { setSide("sell"); setReview(false); }}>Sell / short</button>
            </div></div>
          <div className="field-row"><span>Order type</span>
            <div className="seg" role="group" aria-label="Order type">
              {([["market", "Market"], ["limit", "Limit"], ["post_only", "Post-only"]] as const).map(([k, l]) =>
                <button type="button" key={k} aria-pressed={type === k} onClick={() => { setType(k); setReview(false); }}>{l}</button>)}
            </div></div>
          <label data-help="How much to trade, in USD notional or in units of the asset. USD is converted at the current mid."><span>Amount</span>
            <span className="amount">
              <input type="number" min={0} step="any" value={amount} onChange={(e) => { setAmount(e.target.value); setReview(false); }} />
              <span className="seg" role="group" aria-label="Amount unit">
                <button type="button" aria-pressed={unit === "usd"} onClick={() => setUnit("usd")}>USD</button>
                <button type="button" aria-pressed={unit === "units"} onClick={() => setUnit("units")}>{symbol || "units"}</button>
              </span>
            </span></label>
          {type !== "market" && (
            <label><span>Limit price</span>
              <span className="amount">
                <input type="number" min={0} step="any" value={px} onChange={(e) => { setPx(e.target.value); setReview(false); }} />
                <span className="seg" role="group" aria-label="Price shortcuts">
                  <button type="button" onClick={() => fill("bid")}>Bid</button>
                  <button type="button" onClick={() => fill("mid")}>Mid</button>
                  <button type="button" onClick={() => fill("ask")}>Ask</button>
                </span>
              </span></label>)}
          <label className="switch-row" data-help="Reduce-only: the order may only shrink or close a position on this market, never open or flip one. Allowed even when the kill switch is on.">
            <input type="checkbox" checked={reduce} onChange={(e) => setReduce(e.target.checked)} />
            <span>Reduce-only (close or shrink a position)</span></label>
          {problems.length > 0 && <p className="muted small">{problems[0]}</p>}
          {!review ? <div><button type="submit" className="btn" disabled={problems.length > 0}>Review order</button></div>
            : <div className={`review ${side}`}>
                <p><b>{side === "buy" ? "Buy" : "Sell"} {qty.toPrecision(6)} {symbol}</b> {kind === "spot" ? "spot " : ""}on {V(venue)} ({t?.mode})
                  {type === "market" ? " at market" : ` with a limit at ${price(limit)}`}{reduce ? ", reduce-only" : ""}.</p>
                <p className="muted small">About {usd(notional)} notional, {taker ? "taker" : "maker"} fee about {usd(notional * feeRate)}
                  {type !== "market" && !crosses ? ". It will rest on the book until filled or cancelled." : "."}</p>
                <div className="cell-flex">
                  <button type="button" className={`btn ${side === "sell" ? "btn-stop" : ""}`} disabled={busy} onClick={place}>
                    {busy ? "Sending" : `Place ${side} order`}</button>
                  <button type="button" className="btn-small" onClick={() => setReview(false)}>Back</button>
                </div>
              </div>}
          {result && <Notice kind={result.kind}>{result.text}</Notice>}
        </form>

        <aside className="ticket-side">
          <dl className="kv">
            <dt>Bid</dt><dd className="neg">{price(t?.book?.bid)}</dd>
            <dt>Ask</dt><dd className="pos">{price(t?.book?.ask)}</dd>
            <dt>Spread</dt><dd>{t?.book ? bp((t.book.ask - t.book.bid) / t.book.mid * 1e4) : "–"}</dd>
            <dt>Book age</dt><dd>{t?.book ? `${t.book.age_s.toFixed(1)} s` : "–"}</dd>
            <dt>Funding / yr</dt><dd className={signCls(t?.funding_apr)}>{pct(t?.funding_apr)}</dd>
            <dt>Maker / taker fee</dt><dd>{t ? `${bp((t.maker_fee ?? 0) * 1e4, 2)} / ${bp((t.taker_fee ?? 0) * 1e4, 2)}` : "–"}</dd>
            <dt>Your position</dt><dd className={signCls(t?.position?.size)}>{t?.position ? `${t.position.size > 0 ? "+" : ""}${t.position.size.toPrecision(6)} (${usd(t.position.notional)})` : "none"}</dd>
            <dt>Account equity</dt><dd>{usd(t?.account?.equity)}</dd>
            <dt>Margin in use</dt><dd>{usd(t?.account?.margin_used)}</dd>
            <dt>Order notional</dt><dd>{usd(notional)}</dd>
            <dt>Margin needed</dt><dd>{usd(margin)} {kind === "perp" && t ? <span className="muted">at {t.leverage}x</span> : null}</dd>
            <dt>Estimated fee</dt><dd>{usd(notional * feeRate)} <span className="muted">{taker ? "taker" : "maker"}</span></dd>
          </dl>
          {tk.error && <Notice kind="err">{tk.error}</Notice>}
        </aside>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ hedged pair
function PairTicket({ st }: { st: TStatus | null }) {
  const venues = liveVenues(st);
  const [symbol, setSymbol] = useState("BTC");
  const [longV, setLongV] = useState("");
  const [longKind, setLongKind] = useState<"perp" | "spot">("perp");
  const [shortV, setShortV] = useState("");
  const [amount, setAmount] = useState("100");
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "info" | "err"; text: string } | null>(null);
  useEffect(() => {
    if (!longV && venues.length) setLongV(venues[0]);
    if (!shortV && venues.length > 1) setShortV(venues[1]);
  }, [venues, longV, shortV]);
  useEffect(() => { if (longV !== "hyperliquid") setLongKind("perp"); }, [longV]);
  const longM = useMarkets(longV, longKind);
  const shortM = useMarkets(shortV, "perp");
  const both = useMemo(() => { const sh = new Set(shortM.map((m) => m.symbol)); return longM.filter((m) => sh.has(m.symbol)); }, [longM, shortM]);
  const pick = useCallback((s: string) => { setSymbol(s); setReview(false); }, []);
  const same = longV === shortV && longKind === "perp";
  const problems = [(!symbol || !both.length) && "pick an asset listed on both venues", same && "long and short legs must be on different venues (or spot vs perp on Hyperliquid)",
    !(+amount > 0) && "enter a size per leg", st?.config.kill_switch && "kill switch is on"].filter(Boolean) as string[];
  const open = async () => {
    setBusy(true);
    try {
      const r = await post("/api/trading/manual/pair", { symbol, long_venue: longV, long_kind: longKind, short_venue: shortV, usd: +amount });
      setResult({ kind: r.long.filled && r.short.filled ? "info" : "err",
        text: `Long ${V(longV)}: ${r.long.filled ? `${(+r.long.filled).toPrecision(6)} at ${price(r.long.avg_px)}` : `not filled (${r.long.error ?? "no fill"})`}. ` +
              `Short ${V(shortV)}: ${r.short.filled ? `${(+r.short.filled).toPrecision(6)} at ${price(r.short.avg_px)}` : `not filled (${r.short.error ?? "no fill"})`}.` });
      setReview(false);
    } catch (e) {
      setResult({ kind: "err", text: `Pair not opened: ${(e as Error).message}` });
    } finally { setBusy(false); }
  };
  return (
    <section className="panel">
      <div className="panel-head"><h2>Hedged pair</h2></div>
      <p className="muted small">Buys one leg and sells the other at the same size, maker first, then crossing the spread. The pair appears under Positions, where the imbalance and liquidation brakes watch it and Close shuts both legs.</p>
      <form className="form" onSubmit={(e) => { e.preventDefault(); if (!problems.length) setReview(true); }}>
        <label><span>Long venue</span>
          <select value={longV} onChange={(e) => { setLongV(e.target.value); setReview(false); }}>
            {venues.map((v) => <option key={v} value={v}>{V(v)}</option>)}</select></label>
        {longV === "hyperliquid" && (
          <div className="field-row"><span>Long leg</span>
            <div className="seg" role="group" aria-label="Long leg">
              <button type="button" aria-pressed={longKind === "perp"} onClick={() => setLongKind("perp")}>Perp</button>
              <button type="button" aria-pressed={longKind === "spot"} onClick={() => setLongKind("spot")}>Spot</button>
            </div></div>)}
        <label><span>Short venue</span>
          <select value={shortV} onChange={(e) => { setShortV(e.target.value); setReview(false); }}>
            {venues.map((v) => <option key={v} value={v}>{V(v)}</option>)}</select></label>
        {both.length ? <MarketPicker key={longV + longKind + shortV} label="Asset on both venues" markets={both} value={symbol} onChange={pick} />
          : <p className="muted small">These two venues list no common asset{longKind === "spot" ? " as spot vs perp" : ""}. Pick another pair of venues.</p>}
        <label><span>USD per leg</span><input type="number" min={0} step="any" value={amount} onChange={(e) => { setAmount(e.target.value); setReview(false); }} /></label>
        {problems.length > 0 && <p className="muted small">{problems[0]}</p>}
        {!review ? <div><button type="submit" className="btn" disabled={problems.length > 0}>Review pair</button></div>
          : <div className="review buy">
              <p><b>Long {symbol} {longKind === "spot" ? "spot " : ""}on {V(longV)}, short {symbol} on {V(shortV)}</b>, {usd(+amount)} per leg.</p>
              <p className="muted small">No persistence test: you are choosing this pair yourself. Caps, kill switch and venue modes still apply.</p>
              <div className="cell-flex">
                <button type="button" className="btn" disabled={busy} onClick={open}>{busy ? "Opening" : "Open pair"}</button>
                <button type="button" className="btn-small" onClick={() => setReview(false)}>Back</button>
              </div>
            </div>}
        {result && <Notice kind={result.kind}>{result.text}</Notice>}
      </form>
    </section>
  );
}

export function TicketPage() {
  const t = useTrading();
  const venues = liveVenues(t.data);
  return (
    <>
      <PageHead title="New order" sub="Place orders yourself on any venue that is in paper or testnet mode. They pass the same guard as the bot: kill switch, venue mode, price sanity and size caps." />
      {t.data?.config.kill_switch && <Notice kind="err">Kill switch is ON: only reduce-only orders pass.</Notice>}
      {t.data && !venues.length && <Notice kind="warn">Every venue is off. Set one to paper or testnet under Risk, Limits.</Notice>}
      <Notice>Mainnet does not exist in this build: paper orders are simulated on live prices, testnet orders use play money on the venue's test exchange.</Notice>
      <OrderTicket st={t.data} />
      <PairTicket st={t.data} />
    </>
  );
}
