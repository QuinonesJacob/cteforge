// Role checks for showing/hiding actions (ADR 0018). The server enforces permissions; this
// only avoids offering buttons that would be refused.
import type { DefinitionState, DefinitionVersionInfo, ProposalKind, User, UserRole } from "../api/types";

export const ROLES: UserRole[] = ["viewer", "contributor", "approver", "admin"];

export function roleAtLeast(role: UserRole | undefined, minimum: UserRole): boolean {
  if (!role) return false;
  return ROLES.indexOf(role) >= ROLES.indexOf(minimum);
}

export const canPropose = (u: User | null) => roleAtLeast(u?.role, "contributor");
export const canApprove = (u: User | null) => roleAtLeast(u?.role, "approver");
/** Agents can never verify (ADR 0007), whatever their role. */
export const canVerify = (u: User | null) => canApprove(u) && u?.kind === "human";
export const isAdmin = (u: User | null) => u?.role === "admin";

/**
 * Four-eyes rule (ADR 0018 addendum, trust/approval.py _own_work): the person wrote this work
 * and is not a human admin, so they may not approve or verify it themselves.
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

/** Owner of the target definition, and at least a contributor (trust/approval.py _is_owner). */
export function isTargetOwner(u: User | null, target: DecideTarget | null | undefined): boolean {
  return Boolean(u && target?.owner && target.owner === u.name && roleAtLeast(u.role, "contributor"));
}

/**
 * Approve/reject permission (services/review.py _require_decide → trust/approval.can_approve):
 * approver/admin, or — for definition proposals — the target definition's owner. Changes to a
 * verified definition need a person.
 */
export function canDecide(u: User | null, kind: ProposalKind, target: DecideTarget | null | undefined): boolean {
  if (!u) return false;
  if (!DEFINITION_KINDS.includes(kind)) return canApprove(u);
  if (target?.state === "verified" && u.kind === "agent") return false;
  return canApprove(u) || isTargetOwner(u, target);
}
