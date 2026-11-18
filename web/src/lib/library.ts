// Library filters (ADR 0026): URL params ⇄ filter state ⇄ GET /library query. Pure.
import type { ActorKind, DefinitionState, LibraryFilter, LibraryRow, ProposalBrief, ProposalStatus } from "../api/types";

export const TRUST_OPTIONS: { value: DefinitionState; label: string }[] = [
  { value: "verified", label: "Verified" },
  { value: "llm_annotated", label: "LLM-annotated" },
  { value: "draft", label: "Draft" },
];
export const APPROVAL_OPTIONS: { value: ProposalStatus; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
];
export const KIND_OPTIONS: { value: ActorKind; label: string }[] = [
  { value: "human", label: "Person" },
  { value: "agent", label: "Agent" },
];
/** Rejected and withdrawn are hidden by default (ADR 0026). */
export const DEFAULT_APPROVAL: ProposalStatus[] = ["pending", "approved"];

export interface LibraryFilters {
  q: string;
  trust: DefinitionState[];
  approval: ProposalStatus[];
  createdBy: ActorKind[];
  verifiedBy: ActorKind[];
  needsMyReview: boolean;
}

function pick<T extends string>(raw: string | null, options: { value: T }[]): T[] {
  if (!raw) return [];
  const wanted = new Set(raw.split(","));
  return options.map((o) => o.value).filter((v) => wanted.has(v));
}

/** URL search params → filters. `approval` absent = the default (pending + approved);
 * `approval=` (empty) = none selected. `review=mine` = "Needs my review". */
export function filtersFromParams(params: URLSearchParams): LibraryFilters {
  const approval = params.get("approval");
  return {
    q: params.get("q") ?? "",
    trust: pick(params.get("trust"), TRUST_OPTIONS),
    approval: approval === null ? [...DEFAULT_APPROVAL] : pick(approval, APPROVAL_OPTIONS),
    createdBy: pick(params.get("created"), KIND_OPTIONS),
    verifiedBy: pick(params.get("verified"), KIND_OPTIONS),
    needsMyReview: params.get("review") === "mine",
  };
}

const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

/** Filters → URL search params (defaults left out, so a plain Library URL stays "/"). */
export function paramsFromFilters(f: LibraryFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.trust.length) p.set("trust", f.trust.join(","));
  if (!sameSet(f.approval, DEFAULT_APPROVAL)) p.set("approval", f.approval.join(","));
  if (f.createdBy.length) p.set("created", f.createdBy.join(","));
  if (f.verifiedBy.length) p.set("verified", f.verifiedBy.join(","));
  if (f.needsMyReview) p.set("review", "mine");
  return p;
}

/**
 * Filters → GET /library query. An empty group is no filter on that group; for approval the
 * server's default (pending + approved) is sent as "omitted", and nothing selected means
 * every status (the server would otherwise apply its default).
 */
export function libraryQuery(f: LibraryFilters): LibraryFilter {
  const query: LibraryFilter = {};
  if (f.q.trim()) query.q = f.q.trim();
  if (f.trust.length) query.trust = f.trust;
  if (f.approval.length === 0) query.approval = APPROVAL_OPTIONS.map((o) => o.value);
  else if (!sameSet(f.approval, DEFAULT_APPROVAL)) query.approval = f.approval;
  if (f.createdBy.length) query.created_by = f.createdBy;
  if (f.verifiedBy.length) query.verified_by = f.verifiedBy;
  if (f.needsMyReview) query.needs_my_review = true;
  return query;
}

/** Add or remove `value` from a filter group. */
export function toggle<T extends string>(list: T[], value: T, on: boolean): T[] {
  return on ? (list.includes(value) ? list : [...list, value]) : list.filter((v) => v !== value);
}

/** Whether any filter differs from the default view. */
export function isFiltered(f: LibraryFilters): boolean {
  return paramsFromFilters({ ...f, q: "" }).toString() !== "";
}

/** A row that is a new definition never applied (a proposal, not a library entry). */
export const isProposalOnly = (row: LibraryRow) => row.version == null;

/** The label of a pending proposal badge on a row. */
export function pendingLabel(p: Pick<ProposalBrief, "kind">): string {
  switch (p.kind) {
    case "revise_definition":
      return "Revision pending";
    case "rename_definition":
      return "Rename pending";
    case "new_definition":
      return "Pending approval";
    default:
      return `${p.kind.replace(/_/g, " ")} pending`;
  }
}

/** Where a row links: a stored definition's page, else its (newest) proposal. */
export function rowHref(row: LibraryRow): string {
  if (!isProposalOnly(row)) return `/definitions/${encodeURIComponent(row.id)}`;
  const id = row.latest_proposal?.id ?? row.pending[0]?.id;
  return id ? `/review/${encodeURIComponent(id)}` : `/definitions/${encodeURIComponent(row.id)}`;
}
