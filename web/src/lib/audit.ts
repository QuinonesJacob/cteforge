// The Log page (ADR 0029): audit events → what a row shows. Pure.
import type { AuditActor, AuditEvent } from "../api/types";
import { formatValue } from "./format";

/** The real call name: the audit kind, plus `extra.op` when one kind covers several calls
 * ("propose · revise_definition", "review · approve", "admin · create_user"). */
export function callName(e: Pick<AuditEvent, "kind" | "extra">): string {
  const op = e.extra?.op;
  return typeof op === "string" && op && op !== e.kind ? `${e.kind} · ${op}` : e.kind;
}

export type OutcomeKind = "ok" | "denied" | "error";

/** ok / denied (refused by policy) / error, with the stored message (class name for errors). */
export function outcomeOf(e: Pick<AuditEvent, "outcome" | "error">): { kind: OutcomeKind; message: string | null } {
  const kind: OutcomeKind = e.outcome === "rejected" ? "denied" : e.outcome === "error" ? "error" : "ok";
  return { kind, message: e.error ?? null };
}

export interface LogSummary {
  events: number;
  agents: number;
  people: number;
  errors: number;
  denied: number;
}

/** Counts over the events returned (never more than the page holds). */
export function logSummary(events: AuditEvent[]): LogSummary {
  const agents = new Set<string>();
  const people = new Set<string>();
  let errors = 0;
  let denied = 0;
  for (const e of events) {
    (e.actor_kind === "agent" ? agents : people).add(e.actor);
    if (e.outcome === "error") errors++;
    else if (e.outcome === "rejected") denied++;
  }
  return { events: events.length, agents: agents.size, people: people.size, errors, denied };
}

/** "net_revenue@7" → "net_revenue". */
export const definitionIdOf = (ref: string): string => ref.replace(/@\d+$/, "");

/** Values as `name = value` lines; a definition's values are nested per alias. */
export function valueLines(values: Record<string, unknown>): [string, string][] {
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(values ?? {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && !("start" in (v as object) && "end" in (v as object))) {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) out.push([`${k}.${k2}`, formatValue(v2)]);
    } else out.push([k, formatValue(v)]);
  }
  return out;
}

/** The token the call came in with (12-char prefix only, ADR 0029) and the connection used. */
export function eventContext(e: Pick<AuditEvent, "extra">): { token: string | null; connection: string | null } {
  const x = e.extra ?? {};
  const prefix = typeof x.token_prefix === "string" ? x.token_prefix : null;
  const profile = typeof x.profile === "string" ? x.profile : null;
  return { token: prefix ? `${prefix}…` : null, connection: profile };
}

/** Extra facts worth a line, besides op / token / profile (shown raw, never secrets: the
 * server stores none). */
export function otherExtras(e: Pick<AuditEvent, "extra">): [string, string][] {
  const skip = new Set(["op", "token_id", "token_prefix", "profile"]);
  return Object.entries(e.extra ?? {})
    .filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]);
}

/** The actor's shown name: the display name when the server sent one. */
export function actorName(e: Pick<AuditEvent, "actor">, actors: Record<string, AuditActor> | undefined): string {
  return actors?.[e.actor]?.display_name?.trim() || e.actor;
}
