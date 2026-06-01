const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const EMPTY = "·"; // middle dot: an absent numeric cell, no value to show

/** Parse a YYYY-MM-DD date without going through Date(), which would apply a timezone shift and can
 *  land the label on the wrong calendar day. Pass dates are plain calendar dates. */
function parts(iso: string): { y: number; m: number; d: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  return { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) };
}

export function formatDate(iso: string): string {
  const p = parts(iso);
  if (!p) return iso;
  return `${p.d} ${MONTHS[p.m - 1]} ${p.y}`;
}

export function formatDateShort(iso: string): string {
  const p = parts(iso);
  if (!p) return iso;
  return `${String(p.d).padStart(2, "0")} ${MONTHS[p.m - 1]}`;
}

export function dateValue(iso: string): number {
  const p = parts(iso);
  if (!p) return 0;
  return Date.UTC(p.y, p.m - 1, p.d);
}

export function formatNumber(n: number | null | undefined, digits = 3): string {
  if (n === null || n === undefined || Number.isNaN(n)) return EMPTY;
  return n.toFixed(digits);
}

export function formatPercent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return EMPTY;
  return `${Math.round(fraction * 100)}%`;
}

export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
