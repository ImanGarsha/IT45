import { useState } from "react";
import { LogRow, usePoll } from "../api";
import { PageHead, Table } from "../ui";

export function Events() {
  const l = usePoll<LogRow[]>("/api/logs?limit=500", 5000);
  const [lvl, setLvl] = useState("all");
  const rows = (l.data ?? []).filter((r) => lvl === "all" || r.level === lvl);
  return (
    <>
      <PageHead title="Events" sub="Feed failures, recoveries and settings changes, newest first. Stored in data/it45.db."
        right={<div className="seg" role="group" aria-label="Level">
          {["all", "info", "warn", "error"].map((x) => <button key={x} aria-pressed={lvl === x} onClick={() => setLvl(x)}>{x}</button>)}
        </div>} />
      <Table rows={rows} rowKey={(r, i) => `${r.ts}-${i}`}
        empty={<div className="empty"><h3>No events</h3></div>} cols={[
          { key: "ts", label: "Time", render: (r) => new Date(r.ts * 1000).toLocaleString() },
          { key: "level", label: "Level", render: (r) => <span className={`lvl lvl-${r.level}`}>{r.level}</span> },
          { key: "source", label: "Source" },
          { key: "message", label: "Message" },
        ]} />
    </>
  );
}
