// Import flow: building variable annotations from parsed literals (ADR 0014, 0015).
// Suggestions only — the analyst decides. Nothing becomes changeable by default.
import type {
  Annotation,
  DefinitionMetadata,
  Dialect,
  GrainCandidate,
  LiteralContext,
  LiteralInfo,
  Profile,
  RangeCandidate,
  RangeShape,
  Role,
  VariableType,
} from "../api/types";
import { GRAINS } from "./variableForm";

export const VARIABLE_TYPES: VariableType[] = [
  "date_range",
  "date",
  "text_list",
  "text",
  "number",
  "boolean",
  "grain",
];

/** Contexts that make a changeable variable need a description (ADR 0015). */
export const DESCRIPTION_CONTEXTS: LiteralContext[] = [
  "negated",
  "or_branch",
  "case_condition",
  "outer_join_on",
];

export const CONTEXT_LABELS: Record<LiteralContext, string> = {
  negated: "negated (under NOT)",
  or_branch: "inside an OR branch",
  case_condition: "CASE / IF / FILTER condition",
  join_on: "inner join ON",
  outer_join_on: "outer join ON (filters attached rows only)",
  having: "HAVING (after aggregation)",
  subquery: "inside a subquery",
  value_wrapped: "value wrapped in an expression",
};

export type TypeFamily = "date" | "timestamp" | "number" | "boolean" | "text";

/** Warehouse/compared type string → coarse family. Case-insensitive; params ignored. */
export function typeFamily(sqlType: string | null | undefined): TypeFamily | null {
  if (!sqlType) return null;
  const t = sqlType.toUpperCase();
  if (/BOOL/.test(t)) return "boolean";
  if (/TIMESTAMP|DATETIME/.test(t)) return "timestamp";
  if (/^DATE\b|^DATE$/.test(t)) return "date";
  if (/INT|DECIMAL|NUMERIC|NUMBER|FLOAT|DOUBLE|REAL|BIGNUMERIC|MONEY/.test(t)) return "number";
  if (/CHAR|TEXT|STRING|VARCHAR|UUID/.test(t)) return "text";
  return null;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const NUMERIC = /^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

function literalType(lit: LiteralInfo): TypeFamily | null {
  return typeFamily(lit.compared_type) ?? typeFamily(lit.column?.type) ?? typeFamily(lit.cast_type);
}

export function findRange(
  ranges: RangeCandidate[],
  a: string,
  b: string,
): RangeCandidate | undefined {
  return ranges.find((r) => (r.start === a && r.end === b) || (r.start === b && r.end === a));
}

export function rangeFor(ranges: RangeCandidate[], literalId: string): RangeCandidate | undefined {
  return ranges.find((r) => r.start === literalId || r.end === literalId);
}

/**
 * True when a grain candidate's unit is one a `grain` variable can change (day … year).
 * Others (hour, week(monday), isoweek, a unit read from a column) stay part of the SQL; the
 * backend refuses them in a grain annotation, so the GUI does not offer them.
 */
export function grainUsable(g: GrainCandidate): boolean {
  return (GRAINS as readonly string[]).includes(g.unit);
}

/** Usable grain candidates with the same unit as `g` (every place a grain variable should cover). */
export function sameUnitGrains(grains: GrainCandidate[], g: GrainCandidate): GrainCandidate[] {
  return grains.filter((o) => o.unit === g.unit && grainUsable(o));
}

/** Suggest a variable type from the selected literals (compared type first, then shape).
 * Grain variables are built from grain candidates instead (see newGrainDraft). */
export function suggestType(literals: LiteralInfo[], ranges: RangeCandidate[] = []): VariableType {
  if (literals.length === 0) return "text";
  const first = literals[0]!;
  if (literals.length === 2 && findRange(ranges, first.id, literals[1]!.id)) return "date_range";
  if (literals.every((l) => l.in_list)) return "text_list";
  const families = literals.map(literalType);
  const dateLike = literals.every(
    (l, i) => families[i] === "date" || families[i] === "timestamp" || (families[i] === null && ISO_DATE.test(l.value)),
  );
  if (literals.length > 1 && literals.length % 2 === 0 && dateLike && literals.some((l) => isLowerBound(l.operator)))
    return "date_range";
  const family = families[0];
  switch (family) {
    case "date":
    case "timestamp":
      return "date";
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "text":
      return "text";
    default:
      if (/^(true|false)$/i.test(first.value) && !first.is_string) return "boolean";
      if (!first.is_string && NUMERIC.test(first.value)) return "number";
      if (ISO_DATE.test(first.value)) return "date";
      return "text";
  }
}

export function isLowerBound(operator: string): boolean {
  const op = operator.toUpperCase();
  return op === ">=" || op === ">" || op.endsWith("_LOW");
}

/**
 * Order date_range literals into [start, end] pairs: suggested pairs first, then remaining
 * lower bounds matched with remaining upper bounds in the order they appear.
 */
export function orderRangeLiterals(
  ids: string[],
  ranges: RangeCandidate[],
  byId: Record<string, LiteralInfo>,
): string[] {
  const remaining = [...ids];
  const out: string[] = [];
  for (const r of ranges) {
    if (remaining.includes(r.start) && remaining.includes(r.end)) {
      out.push(r.start, r.end);
      remaining.splice(remaining.indexOf(r.start), 1);
      remaining.splice(remaining.indexOf(r.end), 1);
    }
  }
  const lows = remaining.filter((id) => isLowerBound(byId[id]?.operator ?? ""));
  const highs = remaining.filter((id) => !lows.includes(id));
  const n = Math.max(lows.length, highs.length);
  for (let i = 0; i < n; i++) {
    if (lows[i]) out.push(lows[i]!);
    if (highs[i]) out.push(highs[i]!);
  }
  return out;
}

/** True when every [start, end] pair is a range the parser suggested. */
export function allPairsSuggested(ordered: string[], ranges: RangeCandidate[]): boolean {
  if (ordered.length === 0 || ordered.length % 2 !== 0) return false;
  for (let i = 0; i < ordered.length; i += 2) {
    const r = ranges.find((c) => c.start === ordered[i] && c.end === ordered[i + 1]);
    if (!r) return false;
  }
  return true;
}

/**
 * The shape the backend will record for a date_range over these [start, end] pairs
 * (templating/template.py _range_shape): every pair a suggested range, all of one shape.
 * Null otherwise (the import reports range_not_simple).
 */
export function rangeShape(ordered: string[], ranges: RangeCandidate[]): RangeShape | null {
  if (ordered.length === 0 || ordered.length % 2 !== 0) return null;
  let shape: RangeShape | null = null;
  for (let i = 0; i < ordered.length; i += 2) {
    const r = ranges.find((c) => c.start === ordered[i] && c.end === ordered[i + 1]);
    if (!r || (shape !== null && r.shape !== shape)) return null;
    shape = r.shape;
  }
  return shape;
}

export interface DescriptionRule {
  required: boolean;
  reasons: string[];
}

/**
 * ADR 0015 (templating/template.py _description_issue): only a changeable variable can need
 * a description. It does when its literals (or truncations) carry negated / or_branch /
 * case_condition / outer_join_on context, or when it is a date_range with no recorded shape
 * (bounds not suggested pairs, or pairs of different shapes: range_not_simple).
 */
export function descriptionRule(
  draft: { type: VariableType; role: Role; literals: string[] },
  byId: Record<string, LiteralInfo>,
  ranges: RangeCandidate[],
  grainsById: Record<string, GrainCandidate> = {},
): DescriptionRule {
  const reasons: string[] = [];
  if (draft.role !== "changeable") return { required: false, reasons };
  for (const id of draft.literals) {
    const ctx = byId[id]?.context ?? grainsById[id]?.context ?? [];
    for (const c of ctx) {
      if (DESCRIPTION_CONTEXTS.includes(c)) reasons.push(`${id} is ${CONTEXT_LABELS[c]}`);
    }
  }
  if (draft.type === "date_range" && rangeShape(draft.literals, ranges) === null) {
    reasons.push(
      allPairsSuggested(draft.literals, ranges)
        ? "the range pairs have different shapes, so none is recorded (range_not_simple)"
        : "bounds are not a suggested range pair (range_not_simple)",
    );
  }
  return { required: reasons.length > 0, reasons };
}

const IDENT = /^[a-z][a-z0-9_]*$/;

export function toIdent(raw: string, fallback = "value"): string {
  let s = raw
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!s) s = fallback;
  if (!/^[a-z]/.test(s)) s = `v_${s}`;
  return s;
}

export function isIdent(name: string): boolean {
  return IDENT.test(name) && !name.includes("__");
}

/** Definition id slug from a title. */
export const slugify = (title: string) => toIdent(title, "definition");

function columnName(lit: LiteralInfo): string {
  if (lit.column?.column) return lit.column.column;
  const idents = (lit.column_sql ?? "").match(/[A-Za-z_][A-Za-z0-9_]*/g) ?? [];
  return idents[idents.length - 1] ?? "value";
}

/** Suggest a unique variable name from the first literal's column. */
export function suggestName(literals: LiteralInfo[], type: VariableType, taken: string[] = []): string {
  const first = literals[0];
  let base = toIdent(first ? columnName(first) : "value");
  if (type === "date_range") base = `${base.replace(/_(at|date|day|ts|time)$/, "")}_period`;
  if (type === "grain") base = "grain";
  let name = base;
  for (let n = 2; taken.includes(name); n++) name = `${base}_${n}`;
  return name;
}

/** Comma/newline separated text → trimmed non-empty items. */
export function parseList(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Allowed-values text → typed list for the annotation (null when empty). */
export function parseAllowed(text: string, type: VariableType): unknown[] | null {
  const items = parseList(text);
  if (items.length === 0) return null;
  if (type === "number") return items.map((i) => (NUMERIC.test(i) ? Number(i) : i));
  if (type === "boolean") return items.map((i) => (/^true$/i.test(i) ? true : /^false$/i.test(i) ? false : i));
  return items;
}

export interface VariableDraft {
  name: string;
  type: VariableType;
  role: Role;
  literals: string[];
  column: string;
  description: string;
  allowed: string; // comma separated, as typed
  unit: string;
  format: string;
}

/** Problems that block adding the variable (the backend re-checks everything). */
export function validateDraft(
  draft: VariableDraft,
  byId: Record<string, LiteralInfo>,
  ranges: RangeCandidate[],
  otherNames: string[],
  grainsById: Record<string, GrainCandidate> = {},
): string[] {
  const errors: string[] = [];
  if (!isIdent(draft.name)) errors.push("Name must be lower_snake_case (letters, digits, single underscores).");
  if (otherNames.includes(draft.name)) errors.push(`A variable named '${draft.name}' already exists.`);
  if (draft.literals.length === 0) errors.push("Select at least one literal.");
  if (draft.type === "date_range" && draft.literals.length % 2 !== 0)
    errors.push("A date range needs a start and an end literal (pairs).");
  const grainIds = draft.literals.filter((id) => grainsById[id]);
  if (draft.type === "grain") {
    if (grainIds.length !== draft.literals.length)
      errors.push("A grain variable covers date truncations (grain candidates) only, not literals.");
    const units = new Set(grainIds.map((id) => grainsById[id]!.unit));
    if (units.size > 1) errors.push(`Every truncation must have the same unit, got ${[...units].join(", ")}.`);
  } else if (grainIds.length > 0) {
    errors.push("Date truncations (grain candidates) can only become a grain variable.");
  }
  if (descriptionRule(draft, byId, ranges, grainsById).required && !draft.description.trim())
    errors.push("A description is required for this variable (see why below).");
  return errors;
}

/** Draft → API annotation. Type-specific fields only where they apply. */
export function toAnnotation(draft: VariableDraft): Annotation {
  const ann: Annotation = {
    name: draft.name,
    type: draft.type,
    role: draft.role,
    literals: draft.literals,
    description: draft.description.trim(),
  };
  if (draft.column.trim()) ann.column = draft.column.trim();
  const allowed = parseAllowed(draft.allowed, draft.type);
  if (allowed) ann.allowed = allowed;
  if (draft.type === "number" && draft.unit.trim()) ann.unit = draft.unit.trim();
  if ((draft.type === "date" || draft.type === "date_range") && draft.format.trim())
    ann.format = draft.format.trim();
  return ann;
}

/** New draft for a selection of literals, with suggested type/name/format/column. */
export function newDraft(selected: LiteralInfo[], ranges: RangeCandidate[], taken: string[]): VariableDraft {
  const type = suggestType(selected, ranges);
  const byId = Object.fromEntries(selected.map((l) => [l.id, l]));
  const ids = selected.map((l) => l.id);
  const literals = type === "date_range" ? orderRangeLiterals(ids, ranges, byId) : ids;
  const first = selected[0];
  return {
    name: suggestName(selected, type, taken),
    type,
    role: "fixed",
    literals,
    column: first?.column_sql ?? "",
    description: "",
    allowed: "",
    unit: "",
    format: selected.find((l) => l.suggested_format)?.suggested_format ?? "",
  };
}

/** New grain variable draft over selected date truncations (their grain IDs). */
export function newGrainDraft(selected: GrainCandidate[], taken: string[]): VariableDraft {
  const first = selected[0];
  let name = "grain";
  for (let n = 2; taken.includes(name); n++) name = `grain_${n}`;
  return {
    name,
    type: "grain",
    role: "fixed",
    literals: selected.map((g) => g.id),
    column: first?.column_sql ?? "",
    description: "",
    allowed: "",
    unit: "",
    format: "",
  };
}

// ── Re-parsing: which drafts still point at the same places ──────────────────────

/** Everything the parser reports about where a literal sits (its ID is only a position). */
function literalFingerprint(l: LiteralInfo): string {
  return JSON.stringify([l.scope, l.value, l.is_string, l.form, l.cast_type ?? null, l.operator, l.column_sql,
    l.in_list ?? null, l.wrapped, [...l.context].sort(), l.wrapper_sql ?? null]);
}

function grainFingerprint(g: GrainCandidate): string {
  return JSON.stringify([g.scope, g.unit, g.sql, g.column_sql, g.output ?? null, [...g.context].sort()]);
}

function fingerprints(report: { literals: LiteralInfo[]; grains?: GrainCandidate[] | null }): Map<string, string> {
  const m = new Map<string, string>();
  for (const l of report.literals) m.set(l.id, `L:${literalFingerprint(l)}`);
  for (const g of report.grains ?? []) m.set(g.id, `G:${grainFingerprint(g)}`);
  return m;
}

/**
 * After a re-parse, keep only drafts whose every literal/grain ID names the same thing in
 * both reports. IDs are positional (`scope.L<n>`), so after an SQL edit the same ID can point
 * at a different literal; such drafts are dropped rather than silently rebound.
 */
export function carryOverDrafts(
  drafts: VariableDraft[],
  before: { literals: LiteralInfo[]; grains?: GrainCandidate[] | null } | null,
  after: { literals: LiteralInfo[]; grains?: GrainCandidate[] | null },
): { kept: VariableDraft[]; dropped: VariableDraft[] } {
  if (!before) return { kept: [], dropped: [...drafts] };
  const a = fingerprints(before);
  const b = fingerprints(after);
  const kept: VariableDraft[] = [];
  const dropped: VariableDraft[] = [];
  for (const d of drafts) {
    const same = d.literals.every((id) => a.has(id) && a.get(id) === b.get(id));
    (same ? kept : dropped).push(d);
  }
  return { kept, dropped };
}

// ── Step 3: definition metadata ─────────────────────────────────────────────────

export interface MetadataDraft {
  id: string;
  title: string;
  meaning: string;
  keywords: string; // comma separated
  use_when: string;
  dont_use_when: string;
  grain: string;
  join_keys: string; // comma separated
  owner: string;
  summary: string; // proposal one-liner
}

export const emptyMetadata = (owner = ""): MetadataDraft => ({
  id: "",
  title: "",
  meaning: "",
  keywords: "",
  use_when: "",
  dont_use_when: "",
  grain: "",
  join_keys: "",
  owner,
  summary: "",
});

export function validateMetadata(m: MetadataDraft): Partial<Record<keyof MetadataDraft, string>> {
  const errors: Partial<Record<keyof MetadataDraft, string>> = {};
  if (!m.title.trim()) errors.title = "A title is required.";
  if (!isIdent(m.id)) errors.id = "Use lower_snake_case: letters, digits and single underscores, starting with a letter.";
  if (!m.meaning.trim()) errors.meaning = "Say in plain language what one row means.";
  return errors;
}

export function toMetadata(m: MetadataDraft): DefinitionMetadata {
  return {
    id: m.id.trim(),
    title: m.title.trim(),
    meaning: m.meaning.trim(),
    keywords: parseList(m.keywords),
    use_when: m.use_when.trim(),
    dont_use_when: m.dont_use_when.trim(),
    grain: m.grain.trim() || null,
    join_keys: parseList(m.join_keys),
    owner: m.owner.trim() || null,
  };
}

/**
 * The connection an import uses (ADR 0021): the chosen profile, else the effective default
 * connection (`default: true` from GET /profiles), else the only profile; undefined when
 * none applies (e.g. profiles not loaded yet, or several and no default).
 */
export function importConnection(profiles: Profile[] | null | undefined, profile: string): Profile | undefined {
  const list = profiles ?? [];
  if (profile) return list.find((p) => p.name === profile);
  return list.find((p) => p.default) ?? (list.length === 1 ? list[0] : undefined);
}

/**
 * The Import page's dialect while the person has not picked one: the connection's dialect
 * (the dialect follows the connection, as the server resolves an omitted dialect), else
 * duckdb. `connection` is the profile it came from, if any.
 */
export function defaultImportDialect(
  profiles: Profile[] | null | undefined,
  profile: string,
): { dialect: Dialect; connection: Profile | undefined } {
  const connection = importConnection(profiles, profile);
  return { dialect: connection?.dialect ?? "duckdb", connection };
}

/** The hint under the Dialect field: where the dialect came from, or what a different one costs. */
export function connectionDialectHint(dialect: Dialect, connection: Profile | undefined): string | undefined {
  if (!connection) return undefined;
  if (connection.dialect === dialect) return `From connection ${connection.name}.`;
  return `Differs from connection ${connection.name}: checks run on SQL translated to ${connection.dialect}, lower trust.`;
}
