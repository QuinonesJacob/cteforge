// The tool catalog (domain/access.py) and the built-in roles, mirrored for the GUI (ADR 0027).
// GET /tools is the source of truth for pickers; these constants are the fallback for a user
// object without resolved tools (tests) and keep pickers in catalog order.
import type { BuiltinRole, ToolGroup, ToolInfo, ToolName } from "../api/types";

export const TOOL_GROUPS: Record<ToolGroup, ToolName[]> = {
  use: ["search", "get_definition", "compile_and_run", "check_and_run", "describe_tables", "list_joins", "list_recipes"],
  build: ["parse_query", "propose_definition", "revise_definition", "propose_join", "propose_recipe"],
  review: ["list_proposals", "review_proposal", "verify"],
  admin: ["view_audit", "list_users", "manage_user", "manage_role", "manage_settings"],
};
export const GROUP_ORDER: ToolGroup[] = ["use", "build", "review", "admin"];
export const GROUP_LABELS: Record<ToolGroup, string> = { use: "Use", build: "Build", review: "Review", admin: "Admin" };
export const ALL_TOOLS: ToolName[] = GROUP_ORDER.flatMap((g) => TOOL_GROUPS[g]);
/** Only the admin role may allow these. */
export const ADMIN_ONLY_TOOLS: ToolName[] = ["list_users", "manage_user", "manage_role", "manage_settings"];

const VIEWER: ToolName[] = [...TOOL_GROUPS.use, "list_proposals"];
const CONTRIBUTOR: ToolName[] = [...VIEWER, ...TOOL_GROUPS.build, "review_proposal"];
const APPROVER: ToolName[] = [...CONTRIBUTOR, "verify", "view_audit"];

/** Built-in roles' tools (their defaults equal their allowed lists). */
export const BUILTIN_ROLE_TOOLS: Record<BuiltinRole, ToolName[]> = {
  viewer: sortTools(VIEWER),
  contributor: sortTools(CONTRIBUTOR),
  approver: sortTools(APPROVER),
  admin: ALL_TOOLS,
};
export const BUILTIN_REVIEW_ALL: Record<BuiltinRole, boolean> = {
  viewer: false,
  contributor: false,
  approver: true,
  admin: true,
};

/** A built-in role's tools; undefined for a custom role. */
export const builtinRoleTools = (role: string): ToolName[] | undefined =>
  (BUILTIN_ROLE_TOOLS as Record<string, ToolName[] | undefined>)[role];
export const builtinReviewAll = (role: string): boolean | undefined =>
  (BUILTIN_REVIEW_ALL as Record<string, boolean | undefined>)[role];

/** Tools in catalog order, without duplicates or unknown names. */
export function sortTools(tools: Iterable<string>): ToolName[] {
  const wanted = new Set(tools);
  return ALL_TOOLS.filter((t) => wanted.has(t));
}

/** The catalog grouped for pickers: from GET /tools when loaded, else the constants. */
export function groupCatalog(catalog: ToolInfo[] | null | undefined): { group: ToolGroup; tools: ToolInfo[] }[] {
  const infos: ToolInfo[] =
    catalog && catalog.length
      ? catalog
      : ALL_TOOLS.map((name) => ({
          name,
          group: GROUP_ORDER.find((g) => TOOL_GROUPS[g].includes(name)) ?? "use",
          admin_only: ADMIN_ONLY_TOOLS.includes(name),
        }));
  return GROUP_ORDER.map((group) => ({ group, tools: infos.filter((t) => t.group === group) })).filter(
    (g) => g.tools.length > 0,
  );
}

/** "Use 7/7 · Build 5/5 · Review 3/3 · Admin 0/5": how much of each group a list covers. */
export function toolSummary(tools: ToolName[]): string {
  const set = new Set(tools);
  return GROUP_ORDER.map((g) => {
    const n = TOOL_GROUPS[g].filter((t) => set.has(t)).length;
    return n ? `${GROUP_LABELS[g]} ${n}/${TOOL_GROUPS[g].length}` : "";
  })
    .filter(Boolean)
    .join(" · ") || "No tools";
}

/** A tool name for people: "compile_and_run" → "compile and run". */
export const toolLabel = (t: ToolName) => t.replace(/_/g, " ");

/**
 * The token request for a checklist (ADR 0027 addendum). follow_role: include = checked −
 * defaults, exclude = defaults − checked (so new defaults still follow the role); only:
 * include = checked. Everything is limited to `allowed`.
 */
export function tokenToolsRequest(
  mode: "follow_role" | "only",
  checked: Iterable<ToolName>,
  defaults: ToolName[],
  allowed: ToolName[],
  /** The role's whole Allowed list when `allowed` is narrower (a token session offers only
   * its own tools): role defaults outside `allowed` are then excluded explicitly. */
  ceiling: ToolName[] = allowed,
): { tool_mode: "follow_role" | "only"; include: ToolName[]; exclude: ToolName[] } {
  const ok = new Set(allowed);
  const picked = new Set([...checked].filter((t) => ok.has(t)));
  if (mode === "only") return { tool_mode: "only", include: sortTools(picked), exclude: [] };
  const inRole = new Set(ceiling);
  const base = new Set(defaults.filter((t) => inRole.has(t)));
  return {
    tool_mode: "follow_role",
    include: sortTools([...picked].filter((t) => !base.has(t))),
    exclude: sortTools([...base].filter((t) => !picked.has(t))),
  };
}

/**
 * A date input (YYYY-MM-DD) → the expiry sent to the API: the END of that day in UTC
 * (23:59:59Z), so "expires 2027-01-31" works through that whole day. "" → null (never).
 */
export function expiryFromDate(date: string): string | null {
  if (!date) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  return `${date}T23:59:59Z`;
}

/**
 * The expiry to send when the session's own token expires at `cap` (ADR 0027: a token only
 * generates tokens for its user that expire no later): the end of `date` (UTC), but never
 * after `cap`; no date → `cap` itself. Without a cap this is `expiryFromDate`.
 */
export function expiryWithin(date: string, cap?: string | null): string | null {
  const chosen = expiryFromDate(date);
  if (!cap) return chosen;
  if (!chosen) return cap;
  return Date.parse(chosen) > Date.parse(cap) ? cap : chosen;
}

/** The UTC calendar date (YYYY-MM-DD) of an ISO timestamp, for a date input's `max`. */
export function utcDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

/** Role form rules: admin's Allowed is fixed (every tool); admin-only tools only for admin;
 * defaults ⊆ allowed. Returns the cleaned lists. */
export function cleanRole(name: string, allowed: Iterable<ToolName>, defaults: Iterable<ToolName>): { allowed: ToolName[]; defaults: ToolName[] } {
  if (name === "admin") return { allowed: ALL_TOOLS, defaults: sortTools(defaults) };
  const a = sortTools([...allowed].filter((t) => !ADMIN_ONLY_TOOLS.includes(t)));
  const set = new Set(a);
  return { allowed: a, defaults: sortTools([...defaults].filter((t) => set.has(t))) };
}
