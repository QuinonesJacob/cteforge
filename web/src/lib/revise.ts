// Revising a stored definition (DESIGN §18, POST /proposals/definitions/{id}/revise):
// prefill the import builders from a definition card and send only what changed.
import type { Annotation, DefinitionCard, DefinitionMetadata, GrainCandidate, LiteralInfo } from "../api/types";
import { toMetadata, type MetadataDraft, type VariableDraft } from "./importBuilder";

/** A stored variable (as an annotation from ?include_source=true) → an editable draft. */
export function draftFromAnnotation(a: Annotation): VariableDraft {
  return {
    name: a.name,
    type: a.type,
    role: a.role,
    literals: [...a.literals],
    column: a.column ?? "",
    description: a.description ?? "",
    allowed: (a.allowed ?? []).map((v) => String(v)).join(", "),
    unit: a.unit ?? "",
    format: a.format ?? "",
  };
}

/** The card's metadata as the import form's draft (summary left for the person). */
export function metadataFromCard(d: DefinitionCard): MetadataDraft {
  return {
    id: d.id,
    title: d.title,
    meaning: d.meaning,
    keywords: d.keywords.join(", "),
    use_when: d.use_when,
    dont_use_when: d.dont_use_when,
    grain: d.grain ?? "",
    join_keys: d.join_keys.join(", "),
    owner: d.owner ?? "",
    summary: "",
  };
}

type Changeable = Exclude<keyof DefinitionMetadata, "id">;
const KEYS: Changeable[] = ["title", "meaning", "keywords", "use_when", "dont_use_when", "grain", "join_keys", "owner"];

/** Only the metadata keys whose value differs (the server changes only the keys it is given). */
export function changedMetadata(before: MetadataDraft, after: MetadataDraft): Partial<DefinitionMetadata> {
  const a = toMetadata(before);
  const b = toMetadata(after);
  const out: Partial<DefinitionMetadata> = {};
  for (const k of KEYS) {
    if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) (out as Record<string, unknown>)[k] = b[k];
  }
  return out;
}

/**
 * Seed the variable list from the stored annotations once the rebuilt SQL is parsed: keep a
 * draft only when every ID it names exists in that report (the IDs address the rebuilt SQL,
 * so they should all exist; anything else is dropped rather than guessed).
 */
export function seedDrafts(
  drafts: VariableDraft[],
  report: { literals: LiteralInfo[]; grains?: GrainCandidate[] | null },
): { kept: VariableDraft[]; dropped: VariableDraft[] } {
  const ids = new Set([...report.literals.map((l) => l.id), ...(report.grains ?? []).map((g) => g.id)]);
  const kept: VariableDraft[] = [];
  const dropped: VariableDraft[] = [];
  for (const d of drafts) (d.literals.every((id) => ids.has(id)) ? kept : dropped).push(d);
  return { kept, dropped };
}
