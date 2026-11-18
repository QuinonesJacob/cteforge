// Run-form state per variable type ↔ the strict JSON input formats (DESIGN §4).
// The backend (domain/variables.py) is the validator; this only checks the form is filled in
// and produces exactly the JSON shape each type expects.
import type { DefinitionCard, RunRequest, VariableCard, VariableType } from "../api/types";

export const GRAINS = ["day", "week", "month", "quarter", "year"] as const;
export type Grain = (typeof GRAINS)[number];

export type FormValue =
  | { kind: "date_range"; start: string; end: string }
  | { kind: "date"; value: string }
  | { kind: "text_list"; items: string[] }
  | { kind: "text"; value: string }
  | { kind: "number"; value: string }
  | { kind: "boolean"; value: boolean }
  | { kind: "grain"; value: string };

export type Serialized = { ok: true; value: unknown } | { ok: false; error: string };

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
/** Plain decimal or scientific notation only: no hex/binary/octal, no "Infinity", no "1_000". */
const DECIMAL = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/**
 * Exact value of a decimal string as "<sign><digits>e<exponent>" with no leading/trailing
 * zeros, so two spellings of the same number compare equal ("1000.50" ≡ "1.0005e3").
 */
export function canonicalDecimal(text: string): string {
  const m = /^([-+]?)(\d*)(?:\.(\d*))?(?:[eE]([-+]?\d+))?$/.exec(text.trim());
  if (!m) return text;
  const [, sign = "", int = "", frac = "", exp = "0"] = m;
  let digits = `${int}${frac}`.replace(/^0+/, "");
  if (digits === "") return "0";
  let e = Number(exp) - frac.length;
  const trimmed = digits.replace(/0+$/, "");
  e += digits.length - trimmed.length;
  digits = trimmed;
  return `${sign === "-" ? "-" : ""}${digits}e${e}`;
}

export function isIsoDate(text: string): boolean {
  if (!ISO_DATE.test(text)) return false;
  const d = new Date(`${text}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === text;
}

function str(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : String(value);
}

/** Empty form value for a type. */
export function emptyFormValue(type: VariableType): FormValue {
  switch (type) {
    case "date_range":
      return { kind: "date_range", start: "", end: "" };
    case "date":
      return { kind: "date", value: "" };
    case "text_list":
      return { kind: "text_list", items: [] };
    case "text":
      return { kind: "text", value: "" };
    case "number":
      return { kind: "number", value: "" };
    case "boolean":
      return { kind: "boolean", value: false };
    case "grain":
      return { kind: "grain", value: "month" };
  }
}

/** Form value pre-filled from a plain JSON value (e.g. the variable's original_value). */
export function formValueFrom(type: VariableType, plain: unknown): FormValue {
  switch (type) {
    case "date_range": {
      const v = (plain ?? {}) as { start?: unknown; end?: unknown };
      return { kind: "date_range", start: str(v.start), end: str(v.end) };
    }
    case "date":
      return { kind: "date", value: str(plain) };
    case "text_list":
      return { kind: "text_list", items: Array.isArray(plain) ? plain.map(str) : [] };
    case "text":
      return { kind: "text", value: str(plain) };
    case "number":
      return { kind: "number", value: typeof plain === "number" || typeof plain === "string" ? String(plain) : "" };
    case "boolean":
      return { kind: "boolean", value: plain === true };
    case "grain":
      return { kind: "grain", value: GRAINS.includes(plain as Grain) ? (plain as string) : "month" };
  }
}

export function initialFormValue(card: VariableCard): FormValue {
  return formValueFrom(card.type, card.original_value);
}

/** Form value → the plain JSON value for /run, or a form error. */
export function serializeValue(fv: FormValue): Serialized {
  switch (fv.kind) {
    case "date_range": {
      if (!isIsoDate(fv.start)) return { ok: false, error: "Start date is required (YYYY-MM-DD)." };
      if (!isIsoDate(fv.end)) return { ok: false, error: "End date is required (YYYY-MM-DD)." };
      if (fv.end < fv.start) return { ok: false, error: "End must not be before start." };
      return { ok: true, value: { start: fv.start, end: fv.end } };
    }
    case "date":
      return isIsoDate(fv.value) ? { ok: true, value: fv.value } : { ok: false, error: "A date is required (YYYY-MM-DD)." };
    case "text_list": {
      const items = fv.items.map((i) => i.trim()).filter((i) => i !== "");
      return items.length ? { ok: true, value: items } : { ok: false, error: "Add at least one value." };
    }
    case "text":
      return { ok: true, value: fv.value };
    case "number": {
      const text = fv.value.trim();
      if (text === "") return { ok: false, error: "A number is required." };
      if (!DECIMAL.test(text)) return { ok: false, error: "Enter a decimal number, e.g. 100, -5, 1000.5 or 1e3." };
      const n = Number(text);
      if (!Number.isFinite(n)) return { ok: false, error: "Enter a finite number, e.g. 100 or 1000.5." };
      // JSON numbers are doubles; the backend reads them back exactly (Decimal(repr(x))).
      // Refuse text that a double can't hold, rather than silently sending a different value.
      if (canonicalDecimal(text) !== canonicalDecimal(String(n)))
        return {
          ok: false,
          error: `${text} has more precision than can be sent exactly (it would become ${String(n)}). Enter fewer digits.`,
        };
      return { ok: true, value: n };
    }
    case "boolean":
      return { ok: true, value: fv.value };
    case "grain":
      return GRAINS.includes(fv.value as Grain)
        ? { ok: true, value: fv.value }
        : { ok: false, error: `Choose one of ${GRAINS.join(", ")}.` };
  }
}

/** True when a value is outside the variable's allowed list (warned on, not blocked). */
export function outsideAllowed(value: unknown, allowed: unknown[] | null | undefined): boolean {
  if (!allowed || allowed.length === 0) return false;
  const has = (v: unknown) => allowed.some((a) => a === v || String(a) === String(v));
  return Array.isArray(value) ? value.some((v) => !has(v)) : !has(value);
}

export interface RunValues {
  values: Record<string, unknown>;
  errors: Record<string, string>;
  overridden: string[];
}

/**
 * Collect values for /run: every changeable variable, plus fixed variables only when the
 * person explicitly turned on "override" (which the backend warns on: fixed_override).
 */
export function buildRunValues(
  cards: VariableCard[],
  state: Record<string, FormValue>,
  overrides: ReadonlySet<string>,
): RunValues {
  const out: RunValues = { values: {}, errors: {}, overridden: [] };
  for (const card of cards) {
    const isFixed = card.role === "fixed";
    if (isFixed && !overrides.has(card.name)) continue;
    const fv = state[card.name] ?? initialFormValue(card);
    const result = serializeValue(fv);
    if (result.ok) out.values[card.name] = result.value;
    else out.errors[card.name] = result.error;
    if (isFixed) out.overridden.push(card.name);
  }
  return out;
}

export function buildRunRequest(
  def: Pick<DefinitionCard, "id" | "version">,
  values: Record<string, unknown>,
  opts: { run: boolean; limit: number; profile?: string },
): RunRequest {
  const req: RunRequest = {
    uses: { [def.id]: { id: def.id, version: def.version } },
    values: { [def.id]: values },
    run: opts.run,
    limit: opts.limit,
  };
  if (opts.profile) req.profile = opts.profile;
  return req;
}
