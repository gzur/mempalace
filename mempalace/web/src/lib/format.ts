/** Format helpers used across pages. */

export function formatInt(n: number): string {
  return n.toLocaleString("en-US");
}

export function relativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const seconds = (Date.now() - t) / 1000;
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = hours / 24;
  if (days < 30) return `${Math.round(days)}d ago`;
  return new Date(t).toISOString().slice(0, 10);
}

export function truncate(s: string, max = 80): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}
