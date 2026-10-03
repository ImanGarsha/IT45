import { useEffect, useRef, useState } from "react";
import { lookupHelp } from "./help";

/** Hold-still hover help for every element in the app.
 *
 * Rest the pointer on anything for ~0.45 s (or focus it with the keyboard) and its explanation
 * appears. Resolution, nearest first: an explicit data-help attribute; a table cell's column
 * header; a form label; a button; a stat tile; a link; a heading; an aria-label on a group.
 * Each candidate's visible label is looked up in help.ts; the first hit wins. */

function ownLabel(el: Element): string {
  if (el instanceof HTMLLabelElement) {
    const span = el.querySelector(":scope > span");
    if (span?.textContent) return span.textContent;
    const first = Array.from(el.childNodes).find((n) => n.nodeType === 3 && n.textContent?.trim());
    return first?.textContent ?? "";
  }
  if (el.classList.contains("stat")) return el.querySelector(".stat-label")?.textContent ?? "";
  if (el.classList.contains("vc-row")) return el.querySelector("span")?.textContent ?? "";
  return (el.getAttribute("aria-label") || el.textContent || "").trim();
}

function columnHeader(td: HTMLTableCellElement): string {
  const table = td.closest("table");
  const idx = Array.from(td.parentElement?.children ?? []).indexOf(td);
  const th = table?.querySelectorAll("thead th")[idx];
  return th?.textContent ?? "";
}

export function resolveHelp(start: Element | null): { text: string; anchor: Element } | null {
  let el: Element | null = start;
  while (el && el !== document.body) {
    const explicit = el.getAttribute("data-help");
    if (explicit) return { text: explicit, anchor: el };
    let label = "";
    if (el.tagName === "TD") label = columnHeader(el as HTMLTableCellElement).trim() || ownLabel(el);
    else if (el.matches("th, label, button, a, .stat, .vc-row, h1, h2, h3, dt, [aria-label], .tag"))
      label = ownLabel(el);
    if (label) {
      const t = lookupHelp(label);
      if (t) return { text: t, anchor: el };
    }
    el = el.parentElement;
  }
  return null;
}

// exposed for scripts/help_audit.mjs (lists any visible element without help)
(window as unknown as { __it45Help: typeof resolveHelp }).__it45Help = resolveHelp;

export function useHelpEnabled() {
  const [on, setOn] = useState<boolean>(() => {
    try { return localStorage.getItem("it45-help") !== "0"; } catch { return true; }
  });
  useEffect(() => { try { localStorage.setItem("it45-help", on ? "1" : "0"); } catch { /* */ } }, [on]);
  return [on, setOn] as const;
}

export function HelpLayer({ enabled }: { enabled: boolean }) {
  const [tip, setTip] = useState<{ text: string; x: number; y: number; below: boolean } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const shownFor = useRef<Element | null>(null);

  useEffect(() => {
    if (!enabled) { setTip(null); return; }
    const show = (target: Element | null) => {
      const r = resolveHelp(target);
      if (!r) { setTip(null); shownFor.current = null; return; }
      const b = r.anchor.getBoundingClientRect();
      const below = b.bottom + 120 < window.innerHeight;
      const x = Math.min(Math.max(8, b.left), window.innerWidth - 340);
      setTip({ text: r.text, x, y: below ? b.bottom + 6 : b.top - 6, below });
      shownFor.current = r.anchor;
    };
    const onMove = (e: MouseEvent) => {
      const target = e.target as Element;
      if (shownFor.current && shownFor.current.contains(target)) return;   // still on the same thing
      setTip(null);
      shownFor.current = null;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => show(target), 450);
    };
    const onFocus = (e: FocusEvent) => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => show(e.target as Element), 300);
    };
    const hide = () => { window.clearTimeout(timer.current); setTip(null); shownFor.current = null; };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") hide(); };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("focusin", onFocus);
    document.addEventListener("focusout", hide);
    document.addEventListener("mousedown", hide);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("focusin", onFocus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("mousedown", hide);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hide, true);
      window.clearTimeout(timer.current);
    };
  }, [enabled]);

  if (!tip) return null;
  return (
    <div className="help-tip" role="tooltip"
      style={{ left: tip.x, top: tip.y, transform: tip.below ? undefined : "translateY(-100%)" }}>
      {tip.text}
    </div>
  );
}
