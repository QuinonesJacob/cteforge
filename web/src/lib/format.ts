// Display formatting helpers.
import type { ProposalImpact } from "../api/types";

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  // Plain dates (YYYY-MM-DD) are calendar dates: don't shift them through a time zone.
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

/** Any plain value (cell, original value, allowed list) → short display text. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") return String(value);
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (typeof value === "object") {
    const o = value as Record<string, unknown>;
    if ("start" in o && "end" in o && Object.keys(o).length === 2) return `${formatValue(o.start)} → ${formatValue(o.end)}`;
    return JSON.stringify(value);
  }
  return String(value);
}

/** Impact preview → ids of the composed definitions using the target (ADR 0026). */
export function impactIds(impact: ProposalImpact | undefined): string[] {
  return Array.isArray(impact) ? impact.map(String) : [];
}

export function plural(n: number, word: string, many = `${word}s`): string {
  return `${n} ${n === 1 ? word : many}`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Short age for queues ("now", "5m", "2h", "3d", "4w"). */
export function shortAge(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const ms = Math.max(0, now - t);
  if (ms < MINUTE) return "now";
  if (ms < HOUR) return `${Math.floor(ms / MINUTE)}m`;
  if (ms < DAY) return `${Math.floor(ms / HOUR)}h`;
  if (ms < 14 * DAY) return `${Math.floor(ms / DAY)}d`;
  return `${Math.floor(ms / (7 * DAY))}w`;
}

/** Age in words ("just now", "5 minutes ago", "2 hours ago", "3 days ago"); older → the date. */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const ms = Math.max(0, now - t);
  if (ms < MINUTE) return "just now";
  if (ms < HOUR) return `${plural(Math.floor(ms / MINUTE), "minute")} ago`;
  if (ms < DAY) return `${plural(Math.floor(ms / HOUR), "hour")} ago`;
  if (ms < 30 * DAY) return `${plural(Math.floor(ms / DAY), "day")} ago`;
  return `on ${formatDate(iso)}`;
}

/** Time of day for log rows ("9:14 PM"). */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Up to two initials for an avatar: "A. Nguyen" → "AN", "claude-agent" → "CA". */
export function initials(name: string | null | undefined): string {
  const words = (name ?? "").split(/[\s._-]+/).filter(Boolean);
  if (words.length === 0) return "?";
  const first = words[0] ?? "";
  const last = words[words.length - 1] ?? "";
  const letters = words.length === 1 ? first.slice(0, 2) : (first[0] ?? "") + (last[0] ?? "");
  return letters.toUpperCase();
}

/** A user's shown name: the display name when set (ADR 0029), else the login name. */
export function shownName(u: { name: string; display_name?: string | null } | null | undefined): string {
  if (!u) return "";
  return u.display_name?.trim() || u.name;
}

/** Free text → bullet lines: one per line, list markers ("- ", "* ", "• ") dropped. */
export function bulletLines(text: string | null | undefined): string[] {
  return (text ?? "")
    .split(/\r?\n/)
    .map((l) => l.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, "").trim())
    .filter(Boolean);
}

/** A variable type for people: date_range → "date range", text_list → "list of text". */
export function typeLabel(type: string): string {
  if (type.endsWith("_list")) return `list of ${type.slice(0, -5).replace(/_/g, " ")}`;
  return type.replace(/_/g, " ");
}
