import { useEffect, useState } from "react";
import { putJSON, Settings, usePoll, VenueStatus } from "../api";
import { Notice, PageHead } from "../ui";

function useSettings() {
  const s = usePoll<Settings>("/api/settings", 60000);
  const [msg, setMsg] = useState<{ kind: "info" | "err"; text: string } | null>(null);
  const save = async (patch: Partial<Settings>, what: string) => {
    try { await putJSON("/api/settings", patch); setMsg({ kind: "info", text: `${what} saved.` }); s.reload(); }
    catch (e) { setMsg({ kind: "err", text: `${what} not saved: ${(e as Error).message}` }); }
  };
  return { s: s.data, save, msg };
}

export function General() {
  const { s, save, msg } = useSettings();
  const [f, setF] = useState<Settings | null>(null);
  useEffect(() => { if (s && !f) setF(s); }, [s, f]);
  if (!f) return <PageHead title="General" sub="Loading settings" />;
  const num = (k: keyof Settings) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: +e.target.value });
  return (
    <>
      <PageHead title="General" sub="How often IT45 polls and how the opportunity tables are scored. Saved to data/settings.json." />
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <form className="form" onSubmit={(e) => { e.preventDefault(); save({ poll_seconds: f.poll_seconds, snapshot_seconds: f.snapshot_seconds, horizon_hours: f.horizon_hours, exec_mode: f.exec_mode, min_volume_usd: f.min_volume_usd }, "General settings"); }}>
        <label><span>Poll every (seconds)</span><input type="number" min={2} step={1} value={f.poll_seconds} onChange={num("poll_seconds")} />
          <small>Minimum 2. Bulk allows 5 requests per second per IP and needs one per market.</small></label>
        <label><span>Store a snapshot every (seconds)</span><input type="number" min={10} step={10} value={f.snapshot_seconds} onChange={num("snapshot_seconds")} />
          <small>Funding history for the charts, and later for measuring how long spreads last.</small></label>
        <label><span>Default holding period (hours)</span><input type="number" min={1} value={f.horizon_hours} onChange={num("horizon_hours")} /></label>
        <label><span>Default fee assumption</span>
          <select value={f.exec_mode} onChange={(e) => setF({ ...f, exec_mode: e.target.value as "maker" | "taker" })}>
            <option value="maker">Maker (post-only fills)</option><option value="taker">Taker (market fills)</option>
          </select></label>
        <label><span>Default minimum 24h volume per leg (USD)</span><input type="number" min={0} step={10000} value={f.min_volume_usd} onChange={num("min_volume_usd")} /></label>
        <div><button type="submit" className="btn">Save settings</button></div>
      </form>
    </>
  );
}

export function FeesSettings({ venues }: { venues: VenueStatus[] }) {
  const { s, save, msg } = useSettings();
  const [ov, setOv] = useState<Settings["fee_overrides"] | null>(null);
  useEffect(() => { if (s && !ov) setOv(s.fee_overrides ?? {}); }, [s, ov]);
  if (!ov) return <PageHead title="Fees" sub="Loading settings" />;
  const set = (v: string, k: "maker" | "taker", bpStr: string) => {
    const next = { ...ov, [v]: { ...(ov[v] ?? {}) } };
    if (bpStr === "") delete next[v][k]; else next[v][k] = +bpStr / 1e4;
    if (!Object.keys(next[v]).length) delete next[v];
    setOv(next);
  };
  return (
    <>
      <PageHead title="Fees" sub="Fees in basis points. Leave a box empty to use the venue's base tier. Enter your own tier once your volume earns a discount." />
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <form className="form" onSubmit={(e) => { e.preventDefault(); save({ fee_overrides: ov }, "Fees"); }}>
        <table className="tbl compact">
          <thead><tr><th>Venue</th><th className="num">Base maker</th><th className="num">Base taker</th><th className="num">Your maker (bp)</th><th className="num">Your taker (bp)</th></tr></thead>
          <tbody>
            {venues.map((v) => (
              <tr key={v.name}>
                <td>{v.label}</td>
                <td className="num">{(v.maker_fee * 1e4).toFixed(2)}</td>
                <td className="num">{(v.taker_fee * 1e4).toFixed(2)}</td>
                {(["maker", "taker"] as const).map((k) => (
                  <td key={k} className="num"><input type="number" step="0.01" aria-label={`${v.label} ${k} fee`}
                    value={ov[v.name]?.[k] == null ? "" : +(ov[v.name]![k]! * 1e4).toFixed(4)}
                    onChange={(e) => set(v.name, k, e.target.value)} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <small className="muted">Meridian publishes fees per market; an override here replaces them for every Meridian market. Hyperliquid spot uses its own base tier (4 / 7 bp).</small>
        <div><button type="submit" className="btn">Save fees</button></div>
      </form>
    </>
  );
}

export function VenueSettings({ venues }: { venues: VenueStatus[] }) {
  const { s, save, msg } = useSettings();
  if (!s) return <PageHead title="Venues" sub="Loading settings" />;
  const toggle = (n: string) => {
    const on = s.enabled_venues.includes(n);
    save({ enabled_venues: on ? s.enabled_venues.filter((x) => x !== n) : [...s.enabled_venues, n] }, on ? `${n} disabled` : `${n} enabled`);
  };
  return (
    <>
      <PageHead title="Venues" sub="A disabled venue is not polled and drops out of every matrix and opportunity table." />
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      <div className="form">
        {venues.map((v) => (
          <label key={v.name} className="switch-row">
            <input type="checkbox" checked={s.enabled_venues.includes(v.name)} onChange={() => toggle(v.name)} />
            <span><b>{v.label}</b> <span className="muted">{v.url.replace("https://", "")}</span></span>
          </label>
        ))}
      </div>
    </>
  );
}

export function Keys() {
  return (
    <>
      <PageHead title="API keys" sub="Keys are never typed into the dashboard. They live in a .env file on this computer that git ignores." />
      <Notice kind="warn">
        Not used yet. In M2 each venue gets a trade-only agent key with no withdrawal permission, read from <code>.env</code>.
        Your main wallet keys never go into IT45.
      </Notice>
      <table className="tbl compact">
        <thead><tr><th>Venue</th><th>Key type</th><th>Withdrawal</th></tr></thead>
        <tbody>
          <tr><td>Hyperliquid</td><td>Agent wallet approved by your main wallet</td><td>Agent cannot withdraw</td></tr>
          <tr><td>Arcus</td><td>Ed25519 API key</td><td>Trade keys rejected for withdrawals unless opted in</td></tr>
          <tr><td>Bulk</td><td>Ed25519 agent wallet</td><td>Scope not documented; confirm before funding</td></tr>
          <tr><td>Reya</td><td>EIP-712 signer</td><td>Scope not documented; confirm before funding</td></tr>
          <tr><td>Meridian</td><td>EVM linked signer</td><td>Linked signer cannot withdraw</td></tr>
        </tbody>
      </table>
    </>
  );
}
