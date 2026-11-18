// Join map forms (ADR 0026): edge → annotate / add requests for POST /proposals/joins. Pure.
import type { JoinCard, JoinKey, ProposeJoinRequest, RelationshipEvidence } from "../api/types";

export interface AnnotateDraft {
  relationship: string; // "" = unknown (not sent)
  traps: string; // one per line
  notes: string;
  summary: string;
}

export interface AddDraft {
  left_table: string;
  right_table: string;
  keys: JoinKey[];
  join_type: string;
  relationship: string;
  traps: string;
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

export const emptyAddDraft = (): AddDraft => ({
  left_table: "",
  right_table: "",
  keys: [{ left: "", right: "" }],
  join_type: "inner",
  relationship: "",
  traps: "",
  notes: "",
  summary: "",
});

/** An add request, or the first problem with the form (tables and at least one full key pair). */
export function addRequest(draft: AddDraft): { request: ProposeJoinRequest | null; error: string | null } {
  const left = draft.left_table.trim();
  const right = draft.right_table.trim();
  if (!left || !right) return { request: null, error: "Name both tables." };
  const keys = draft.keys
    .map((k) => ({ left: k.left.trim(), right: k.right.trim() }))
    .filter((k) => k.left || k.right);
  if (keys.length === 0) return { request: null, error: "Add at least one key pair." };
  if (keys.some((k) => !k.left || !k.right)) return { request: null, error: "Each key pair needs both columns." };
  const request: ProposeJoinRequest = {
    action: "add",
    left_table: left,
    right_table: right,
    keys,
    join_type: draft.join_type || "inner",
    summary: draft.summary.trim(),
  };
  if (draft.relationship) request.relationship = draft.relationship;
  const traps = lines(draft.traps);
  if (traps.length) request.traps = traps;
  if (draft.notes.trim()) request.notes = draft.notes.trim();
  return { request, error: null };
}

/** "one_to_many" → "one to many". */
export const relationshipLabel = (r: string | null | undefined) => (r ? r.replace(/_/g, " ") : "unknown");

/** "orders: 120 rows, 100 distinct keys (many)". */
export function sideSummary(side: RelationshipEvidence["left"]): string {
  const kind = side.rows === side.distinct_keys ? "one" : "many";
  return `${side.table}: ${side.rows} rows, ${side.distinct_keys} distinct keys (${kind})`;
}
