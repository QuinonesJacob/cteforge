// Permission checks for showing/hiding actions (ADR 0018, 0027). The permission unit is the
// tool: the signed-in user's `tools` from GET /me (the token's effective tools). The server
// enforces everything; this only avoids offering controls that would be refused.
import type { DefinitionState, DefinitionVersionInfo, ProposalKind, ToolName, User } from "../api/types";
import { builtinReviewAll, builtinRoleTools } from "./tools";

/** Effective tools: from /me, else the built-in role's (trust/approval.py tools_of). */
export function toolsOf(u: User | null | undefined): ToolName[] {
  if (!u || u.active === false) return [];
  const tools = u.tools ?? builtinRoleTools(u.role) ?? [];
  // An agent verifies only with the admin role (ADR 0027).
  return u.kind === "agent" && u.role !== "admin" ? tools.filter((t) => t !== "verify") : tools;
}

export function can(u: User | null | undefined, tool: ToolName): boolean {
  return toolsOf(u).includes(tool);
}

/** Approve/reject anything (not only what they own): review_all (built-in approver, admin). */
export function reviewsAll(u: User | null | undefined): boolean {
  if (!u) return false;
  return u.review_all ?? builtinReviewAll(u.role) ?? false;
}

/** Import / revise / the Edit tab: parse and propose. */
export const canPropose = (u: User | null) => can(u, "parse_query") && can(u, "propose_definition");
export const canRevise = (u: User | null) => can(u, "revise_definition");
/** Annotate or add join-map edges (ADR 0026). */
export const canProposeJoin = (u: User | null) => can(u, "propose_join");
/** Approve or reject any proposal (review_proposal with review_all). */
export const canApprove = (u: User | null) => can(u, "review_proposal") && reviewsAll(u);
/** The verify tool; an agent only with the admin role (toolsOf drops it otherwise). */
export const canVerify = (u: User | null) => can(u, "verify");
export const isAdmin = (u: User | null) => u?.role === "admin";

/**
 * Four-eyes rule (ADR 0018 addendum, ADR 0027; trust/approval.py _own_work): the person wrote
 * this work and is not a human admin, so they may not approve or verify it themselves.
 */
export function isOwnWork(u: User | null, author: string | null | undefined): boolean {
  if (!u || !author || author !== u.name) return false;
  return !(u.role === "admin" && u.kind === "human");
}

/**
 * Everyone who wrote what `version` contains (services/review.py _version_authors): the
 * author of the newest logic-changing version up to it and of every metadata-only one since.
 */
export function versionAuthors(history: Pick<DefinitionVersionInfo, "version" | "author" | "logic_changed">[], version: number): string[] {
  const authors: string[] = [];
  for (const info of [...history].sort((a, b) => b.version - a.version)) {
    if (info.version > version) continue;
    if (!authors.includes(info.author)) authors.push(info.author);
    if (info.logic_changed) break;
  }
  return authors;
}

/** Kinds whose target is a definition (services/review.py; a rename too, ADR 0024). */
export const DEFINITION_KINDS: ProposalKind[] = ["new_definition", "revise_definition", "rename_definition"];

/** The target definition as the GUI knows it (null when it does not exist or is unknown). */
export interface DecideTarget {
  owner?: string | null;
  state?: DefinitionState | null;
}

/** Owner of the target definition, holding review_proposal (trust/approval.py _is_owner). */
export function isTargetOwner(u: User | null, target: DecideTarget | null | undefined): boolean {
  return Boolean(u && target?.owner && target.owner === u.name && can(u, "review_proposal"));
}

/**
 * Approve/reject permission (services/review.py _require_decide → trust/approval.can_approve):
 * review_proposal with review_all, or — for definition proposals — the target definition's
 * owner. Changes to verified work need a person or an admin agent (ADR 0027).
 */
export function canDecide(u: User | null, kind: ProposalKind, target: DecideTarget | null | undefined): boolean {
  if (!u || !can(u, "review_proposal")) return false;
  if (!DEFINITION_KINDS.includes(kind)) return canApprove(u);
  if (target?.state === "verified" && u.kind === "agent" && u.role !== "admin") return false;
  return canApprove(u) || isTargetOwner(u, target);
}

/** Settings sections (ADR 0027). Everyone has "My tokens". */
export const canSeeUsers = (u: User | null) => can(u, "list_users");
export const canManageUsers = (u: User | null) => can(u, "manage_user");
export const canManageRoles = (u: User | null) => can(u, "manage_role");
export const canManageSettings = (u: User | null) => can(u, "manage_settings");
/** Generating tokens: never an agent (agents never see tokens; ADR 0027). */
export const canMintOwnToken = (u: User | null) => Boolean(u && u.kind === "human" && u.active !== false);
