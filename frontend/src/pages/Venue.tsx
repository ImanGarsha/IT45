import { useState } from "react";
import { Quote, usePoll, VenueStatus } from "../api";
import { ago, Dot, fee, Notice, PageHead, pct, price, signCls, Stat, Table, usd } from "../ui";

export function VenuePage({ name, venues }: { name: string; venues: VenueStatus[] }) {
  const v = venues.find((x) => x.name === name);
  const q = usePoll<Quote[]>(`/api/venues/${name}/markets`, 5000);
  const [kind, setKind] = useState<"perp" | "spot">("perp");
  const [filter, setFilter] = useState("");
  const rows = (q.data ?? []).filter((x) => x.kind === kind && x.symbol.toLowerCase().includes(filter.toLowerCase()));
  return (
    <>
      <PageHead title={v?.label ?? name}
        sub={<>Public market data from <a href={v?.url} target="_blank" rel="noreferrer">{v?.url?.replace("https://", "")}</a>. Balances, positions and orders appear here once API keys are connected in M2.</>}
        right={<div className="controls">
          <input type="search" placeholder="Filter markets" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter markets" />
          {v && v.spot > 0 && (
            <div className="seg" role="group" aria-label="Market type">
              <button aria-pressed={kind === "perp"} onClick={() => setKind("perp")}>Perps</button>
              <button aria-pressed={kind === "spot"} onClick={() => setKind("spot")}>Spot</button>
            </div>
          )}
        </div>} />
      {v && (
        <div className="stats">
          <Stat label="Feed" value={<span className="cell-flex"><Dot ok={v.ok} stale={v.stale} />{v.ok ? (v.stale ? "Stale" : "Live") : "Down"}</span>}
            hint={`updated ${ago(v.age_s)}${v.latency_ms ? `, ${v.latency_ms.toFixed(0)} ms` : ""}`} />
          <Stat label="Markets" value={v.perps} hint={v.spot ? `+ ${v.spot} spot pairs` : "perpetuals"} />
          <Stat label="Maker / taker fee" value={`${fee(v.maker_fee)} / ${fee(v.taker_fee)}`} hint="base tier; edit in Settings, Fees" />
          <Stat label="Account" value="Not connected" hint="trade-only key goes in .env (M2)" />
        </div>
      )}
      {v?.error && <Notice kind="err">Last poll failed: {v.error}</Notice>}
      <Table rows={rows} rowKey={(r) => r.raw_symbol} initialSort={["volume_24h_usd", -1]} cols={[
        { key: "symbol", label: "Asset", render: (r) => <span><b>{r.symbol}</b> <span className="muted">{r.raw_symbol}</span></span> },
        { key: "category", label: "Class" },
        { key: "px", label: "Price", num: true, render: (r) => price(r.px) },
        { key: "mark", label: "Mark", num: true, render: (r) => price(r.mark) },
        { key: "oracle", label: "Oracle", num: true, render: (r) => price(r.oracle) },
        ...(kind === "perp" ? [
          { key: "funding_apr", label: "Funding / yr", num: true, render: (r: Quote) => <span className={signCls(r.funding_apr)}>{pct(r.funding_apr)}</span> },
          { key: "funding_1h", label: "Funding / h", num: true, render: (r: Quote) => r.funding_1h == null ? "–" : `${(r.funding_1h * 1e4).toFixed(3)} bp` },
          { key: "open_interest_usd", label: "Open interest", num: true, render: (r: Quote) => usd(r.open_interest_usd) },
          { key: "max_leverage", label: "Max lev.", num: true, render: (r: Quote) => r.max_leverage ? `${r.max_leverage.toFixed(0)}x` : "–" },
        ] : []),
        { key: "volume_24h_usd", label: "24h volume", num: true, render: (r) => usd(r.volume_24h_usd) },
        { key: "status", label: "Status", render: (r) => r.status === "trading" ? "trading" : <span className="neg">{r.status}</span> },
      ]} />
    </>
  );
}
