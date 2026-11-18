// Join map (ADR 0026, 0029 list view): annotate requests for POST /proposals/joins, and the
// list's columns — many/one side, join-type variants, trust of the sources, search, sort. Pure.
import type { DefinitionState, JoinCard, JoinKey, JoinSource, ProposeJoinRequest, RelationshipEvidence } from "../api/types";

export interface AnnotateDraft {
  relationship: string; // "" = unknown (not sent)
  traps: string; // one per line
  notes: string;
  summary: string;
}

/** Non-empty trimmed lines. */
export function lines(text: string): string[] {
  return text
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);
}

export function annotateDraft(edge: JoinCard): AnnotateDraft {
  return { relationship: edge.relationship ?? "", traps: edge.traps.join("\n"), notes: edge.notes ?? "", summary: "" };
}

/**
 * An annotate request with only what changed (relationship in the edge's own orientation,
 * left_table → right_table). null when nothing changed.
 */
export function annotateRequest(edge: JoinCard, draft: AnnotateDraft): ProposeJoinRequest | null {
  const req: ProposeJoinRequest = { action: "annotate", id: edge.id, summary: draft.summary.trim() };
  let changed = false;
  if (draft.relationship && draft.relationship !== (edge.relationship ?? "")) {
    req.relationship = draft.relationship;
    changed = true;
  }
  const traps = lines(draft.traps);
  if (traps.join("\n") !== edge.traps.join("\n")) {
    req.traps = traps;
    changed = true;
  }
  if (draft.notes.trim() !== (edge.notes ?? "").trim()) {
    req.notes = draft.notes.trim();
    changed = true;
  }
  return changed ? req : null;
}

/** "one_to_many" → "one to many". */
export const relationshipLabel = (r: string | null | undefined) => (r ? r.replace(/_/g, " ") : "unknown");

/** "orders: 120 rows, 100 distinct keys (many)". */
export function sideSummary(side: RelationshipEvidence["left"]): string {
  const kind = side.rows === side.distinct_keys ? "one" : "many";
  return `${side.table}: ${side.rows} rows, ${side.distinct_keys} distinct keys (${kind})`;
}

/** "invoice_id = id, region_id = id" (left column = right column). */
export function joinKeysText(keys: JoinKey[], flip = false): string {
  return keys.map((k) => (flip ? `${k.right} = ${k.left}` : `${k.left} = ${k.right}`)).join(", ");
}

export interface JoinSides {
  /** Column headers' meaning: "many"/"one" from the relationship, else plain left/right. */
  kind: "many_one" | "one_one" | "many_many" | "unknown";
  first: string;
  second: string;
  /** Keys written first-side column = second-side column. */
  keys: string;
}

/**
 * Many side · one side from the relationship (ADR 0029). many_to_one: left is many;
 * one_to_many: right is many. one_to_one / many_to_many / unknown keep left → right.
 */
export function joinSides(edge: Pick<JoinCard, "left_table" | "right_table" | "keys" | "relationship">): JoinSides {
  const r = edge.relationship ?? "";
  if (r === "one_to_many")
    return { kind: "many_one", first: edge.right_table, second: edge.left_table, keys: joinKeysText(edge.keys, true) };
  const kind = r === "many_to_one" ? "many_one" : r === "one_to_one" ? "one_one" : r === "many_to_many" ? "many_many" : "unknown";
  return { kind, first: edge.left_table, second: edge.right_table, keys: joinKeysText(edge.keys) };
}

export interface TypeVariant {
  type: string;
  /** Definitions joining this way. */
  count: number;
}

/**
 * Join-type variants (ADR 0029: "LEFT 5 · INNER 1"): how many source definitions join with
 * each type, most used first. Without per-source data, the edge's own type.
 */
export function typeVariants(edge: Pick<JoinCard, "join_type" | "usage_count"> & { source_joins?: JoinSource[] }): TypeVariant[] {
  const src = edge.source_joins ?? [];
  if (src.length === 0) return [{ type: edge.join_type, count: edge.usage_count }];
  const byType = new Map<string, Set<string>>();
  for (const s of src) {
    const t = (s.join_type || "inner").toLowerCase();
    if (!byType.has(t)) byType.set(t, new Set());
    byType.get(t)!.add(s.definition);
  }
  return [...byType.entries()]
    .map(([type, defs]) => ({ type, count: defs.size }))
    .sort((a, b) => b.count - a.count || a.type.localeCompare(b.type));
}

export interface TrustMix {
  verified: number;
  llm_annotated: number;
  draft: number;
}

/** Source definitions by state (the trust bar). Sources without a known state are skipped. */
export function trustMix(edge: Pick<JoinCard, "sources"> & { source_states?: Record<string, DefinitionState> }): TrustMix {
  const mix: TrustMix = { verified: 0, llm_annotated: 0, draft: 0 };
  for (const s of edge.sources) {
    const state = edge.source_states?.[s];
    if (state && state in mix) mix[state] += 1;
  }
  return mix;
}

/** "3 verified, 2 LLM-annotated, 1 draft" (the trust bar's accessible name). */
export function trustMixLabel(mix: TrustMix): string {
  const parts = [
    mix.verified ? `${mix.verified} verified` : "",
    mix.llm_annotated ? `${mix.llm_annotated} LLM-annotated` : "",
    mix.draft ? `${mix.draft} draft` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "no source definitions";
}

/** Where a definition makes the join: "CTE monthly", "main query", "subquery in CTE x". */
export function locationLabel(src: Pick<JoinSource, "location" | "scope">): string {
  if (src.location === "cte") return src.scope ? `CTE ${src.scope}` : "a CTE";
  if (src.location === "subquery") return src.scope ? `subquery in CTE ${src.scope}` : "subquery";
  return "main query";
}

/** Every search word appears in the tables, keys, sources (ids or titles), traps or notes. */
export function matchesJoin(edge: JoinCard, q: string): boolean {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const text = [
    edge.left_table,
    edge.right_table,
    joinKeysText(edge.keys),
    edge.relationship ?? "",
    ...edge.sources,
    ...Object.values(edge.source_titles ?? {}),
    ...edge.traps,
    edge.notes ?? "",
  ]
    .join(" ")
    .toLowerCase();
  return words.every((w) => text.includes(w));
}

export type JoinSortKey = "first" | "second" | "keys" | "type" | "used_by" | "trust";

/** Sort for the list's column headers (stable; ties by edge id). */
export function sortJoins(edges: JoinCard[], key: JoinSortKey, dir: "asc" | "desc"): JoinCard[] {
  const value = (e: JoinCard): string | number => {
    switch (key) {
      case "first":
        return joinSides(e).first;
      case "second":
        return joinSides(e).second;
      case "keys":
        return joinSides(e).keys;
      case "type":
        return typeVariants(e)
          .map((v) => v.type)
          .join(",");
      case "used_by":
        return e.usage_count;
      case "trust": {
        const m = trustMix(e);
        const total = m.verified + m.llm_annotated + m.draft;
        return total ? (m.verified * 2 + m.llm_annotated) / total : -1;
      }
    }
  };
  const sign = dir === "asc" ? 1 : -1;
  return [...edges].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    const c = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
    return c * sign || a.id.localeCompare(b.id);
  });
}
