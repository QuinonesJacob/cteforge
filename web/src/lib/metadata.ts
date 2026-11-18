// A definition proposal's described-field changes (ADR 0029, ProposalView.metadata_changes). Pure.

const LABELS: Record<string, string> = {
  title: "Title",
  meaning: "Meaning",
  keywords: "Keywords",
  use_when: "Use when",
  dont_use_when: "Don't use when",
  grain: "Grain",
  join_keys: "Join keys",
  owner: "Owner",
  profile: "Connection",
  source_dialect: "Dialect",
  variables: "Variables",
};

export const fieldLabel = (field: string): string => LABELS[field] ?? field.replace(/_/g, " ");

/** For two lists: the items only in `after` (added) and only in `before` (removed); null
 * when either side is not a list (then show before → after). */
export function listDelta(before: unknown, after: unknown): { added: string[]; removed: string[] } | null {
  const b = before === null || before === undefined ? [] : before;
  if (!Array.isArray(b) || !Array.isArray(after)) return null;
  const bs = b.map(String);
  const as = after.map(String);
  return { added: as.filter((x) => !bs.includes(x)), removed: bs.filter((x) => !as.includes(x)) };
}

/** The waiting panel's line counts (ADR 0029): "+4 −1", "metadata only", or nothing (a
 * rename or a join has no query). */
export function queryChangeLabel(added: number | null | undefined, removed: number | null | undefined): string | null {
  if (added == null || removed == null) return null;
  if (added === 0 && removed === 0) return "metadata only";
  return `+${added} −${removed}`;
}
