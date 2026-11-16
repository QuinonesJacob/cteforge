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

/** Impact preview → list of affected recipe ids. */
export function impactRecipes(impact: ProposalImpact | undefined): string[] {
  if (!impact) return [];
  if (Array.isArray(impact)) return impact.map(String);
  return Array.isArray(impact.recipes) ? impact.recipes.map(String) : [];
}

export function impactCount(impact: ProposalImpact | undefined): number {
  if (impact && !Array.isArray(impact) && typeof impact.count === "number") return impact.count;
  return impactRecipes(impact).length;
}

export function plural(n: number, word: string, many = `${word}s`): string {
  return `${n} ${n === 1 ? word : many}`;
}
