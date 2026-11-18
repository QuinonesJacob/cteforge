// Rename ID flow (ADR 0024): pure rules for the form; the server re-checks everything.
import type { DefinitionReferences } from "../api/types";
import { isIdent } from "./importBuilder";

export interface RenameCheck {
  /** Submit is enabled only when this is true. */
  ready: boolean;
  /** Why the new ID can't be used (shown under the field); null when it can or is empty. */
  idError: string | null;
}

/**
 * The new ID must be a valid ID other than the current one, and the person must type it
 * again exactly to confirm. Existence and former IDs are checked by the server.
 */
export function checkRename(currentId: string, newId: string, confirm: string): RenameCheck {
  const id = newId.trim();
  let idError: string | null = null;
  if (id && !isIdent(id)) idError = "Use lower_snake_case: letters, digits and single underscores, starting with a letter.";
  else if (id && id === currentId) idError = "That is the current ID.";
  return { ready: Boolean(id) && idError === null && confirm === id, idError };
}

/** How many things inside cteforge name the ID. */
export function referenceCount(refs: DefinitionReferences | undefined | null): number {
  if (!refs) return 0;
  return refs.recipes.length + refs.proposals.length;
}

/** Plain-language form of a recipe reference: which version it uses. */
export function recipeUse(pinned: number | null | undefined): string {
  return pinned == null ? "follows the latest version" : `pins v${pinned}`;
}
