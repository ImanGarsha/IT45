import { useCallback, useEffect, useRef, useState } from "react";

export type Quote = {
  venue: string; symbol: string; raw_symbol: string; kind: "perp" | "spot"; category: string;
  px: number | null; mid: number | null; mark: number | null; oracle: number | null;
  bid: number | null; ask: number | null; funding_1h: number | null; funding_apr: number | null;
  next_funding_1h: number | null; volume_24h_usd: number | null; open_interest_usd: number | null;
  maker_fee: number | null; taker_fee: number | null; max_leverage: number | null; status: string;
};

export type VenueStatus = {
  name: string; label: string; url: string; maker_fee: number; taker_fee: number;
  ok: boolean; enabled: boolean; stale: boolean; age_s: number | null; latency_ms: number | null;
  error: string | null; markets: number; perps: number; spot: number; fails: number;
};

export type Opp = {
  kind: "cross" | "carry"; symbol: string; long_venue: string; short_venue: string;
  long_leg: string; long_funding_apr: number; short_funding_apr: number; edge_apr: number;
  cost_bp: number; net_apr: number; net_bp: number; breakeven_h: number | null;
  basis_bp: number | null; min_volume_usd: number; flags: string[];
};
export type OppResp = { horizon_hours: number; mode: string; min_volume_usd: number; rows: Opp[] };

export type MatrixRow = {
  symbol: string; category: string; spread_apr: number | null; px_dispersion_bp: number | null;
  venues: Record<string, { px: number | null; funding_apr: number | null;
    volume_24h_usd: number | null; status: string; raw_symbol: string }>;
};

export type Health = { version: string; cycles: number; mode: string; venues_ok: number;
  venues_enabled: number; snapshot_rows: number; since: number | null };

export type LogRow = { ts: number; level: string; source: string; message: string };

export type Settings = {
  poll_seconds: number; snapshot_seconds: number; horizon_hours: number;
  exec_mode: "maker" | "taker"; min_volume_usd: number; enabled_venues: string[];
  fee_overrides: Record<string, { maker?: number; taker?: number }>;
};

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${url}`);
  return r.json();
}

export async function putJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail ?? `${r.status}`);
  return j;
}

/** Poll an endpoint every `ms`. Keeps the last good data when a poll fails. */
export function usePoll<T>(url: string | null, ms = 4000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const urlRef = useRef(url);
  urlRef.current = url;
  const load = useCallback(async () => {
    if (!urlRef.current) return;
    try { setData(await getJSON<T>(urlRef.current)); setError(null); }
    catch (e) { setError(String((e as Error).message)); }
  }, []);
  useEffect(() => {
    setData(null);
    load();
    const id = setInterval(load, ms);
    return () => clearInterval(id);
  }, [url, ms, load]);
  return { data, error, reload: load };
}

export const VENUE_ORDER = ["hyperliquid", "arcus", "bulk", "reya", "meridian"];
export const VENUE_LABEL: Record<string, string> = {
  hyperliquid: "Hyperliquid", arcus: "Arcus", bulk: "Bulk", reya: "Reya", meridian: "Meridian",
};
