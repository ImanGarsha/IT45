// 18px stroke glyphs, drawn for this app (no icon font).
const P: Record<string, string> = {
  overview: "M3 13h5V3H3zM10 17h5V7h-5zM3 17h5v-2H3zM10 5h5V3h-5z",
  venues: "M2 7l7-4 7 4M3 7v8M15 7v8M6.5 9v5M11.5 9v5M2 16h14",
  markets: "M2 15l4-5 3 3 4-6 3 3M2 3v13h14",
  opps: "M3 6h9l-3-3M15 12H6l3 3",
  strategies: "M4 3v12M14 3v12M4 6h6a2 2 0 0 1 0 4H4M14 12H9",
  orders: "M3 4h12M3 9h12M3 14h7",
  risk: "M9 2l6 3v4c0 4-3 6.5-6 7.5C6 15.5 3 13 3 9V5z",
  logs: "M4 2h7l3 3v11H4zM7 8h4M7 11h4",
  settings: "M9 6.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM9 1.5v2M9 14.5v2M1.5 9h2M14.5 9h2M3.7 3.7l1.4 1.4M12.9 12.9l1.4 1.4M3.7 14.3l1.4-1.4M12.9 5.1l1.4-1.4",
  chevron: "M6 4l4 5-4 5",
  collapse: "M11 4L6 9l5 5",
  sun: "M9 5.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7zM9 1v2M9 15v2M1 9h2M15 9h2",
  moon: "M14 11.5A6 6 0 0 1 6.5 4a6 6 0 1 0 7.5 7.5z",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" stroke="currentColor"
      strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name] ?? ""} />
    </svg>
  );
}
