import { describe, expect, it } from "vitest";
import type { VariableCard } from "../api/types";
import {
  buildRunRequest,
  buildRunValues,
  canonicalDecimal,
  emptyFormValue,
  formValueFrom,
  initialFormValue,
  isIsoDate,
  outsideAllowed,
  serializeValue,
} from "./variableForm";

function card(partial: Partial<VariableCard> & Pick<VariableCard, "name" | "type">): VariableCard {
  return { role: "changeable", input_format: "", description: "", original_value: null, ...partial };
}

describe("form generation from VariableCards", () => {
  it("pre-fills each type from original_value", () => {
    expect(initialFormValue(card({ name: "p", type: "date_range", original_value: { start: "2026-01-01", end: "2026-04-01" } }))).toEqual({
      kind: "date_range",
      start: "2026-01-01",
      end: "2026-04-01",
    });
    expect(initialFormValue(card({ name: "d", type: "date", original_value: "2026-02-03" }))).toEqual({ kind: "date", value: "2026-02-03" });
    expect(initialFormValue(card({ name: "r", type: "text_list", original_value: ["EMEA", "APAC"] }))).toEqual({
      kind: "text_list",
      items: ["EMEA", "APAC"],
    });
    expect(initialFormValue(card({ name: "n", type: "number", original_value: 1000.5 }))).toEqual({ kind: "number", value: "1000.5" });
    expect(initialFormValue(card({ name: "b", type: "boolean", original_value: true }))).toEqual({ kind: "boolean", value: true });
    expect(initialFormValue(card({ name: "g", type: "grain", original_value: "week" }))).toEqual({ kind: "grain", value: "week" });
    expect(initialFormValue(card({ name: "t", type: "text", original_value: "won" }))).toEqual({ kind: "text", value: "won" });
  });

  it("falls back safely on missing or odd original values", () => {
    expect(formValueFrom("date_range", null)).toEqual({ kind: "date_range", start: "", end: "" });
    expect(formValueFrom("text_list", "x")).toEqual({ kind: "text_list", items: [] });
    expect(formValueFrom("grain", "fortnight")).toEqual({ kind: "grain", value: "month" });
    expect(emptyFormValue("number")).toEqual({ kind: "number", value: "" });
  });
});

describe("serialization per type", () => {
  it("date_range -> {start, end} ISO", () => {
    expect(serializeValue({ kind: "date_range", start: "2026-07-01", end: "2026-10-01" })).toEqual({
      ok: true,
      value: { start: "2026-07-01", end: "2026-10-01" },
    });
    expect(serializeValue({ kind: "date_range", start: "2026-07-01", end: "" }).ok).toBe(false);
    expect(serializeValue({ kind: "date_range", start: "2026-10-01", end: "2026-07-01" })).toEqual({
      ok: false,
      error: "End must not be before start.",
    });
  });

  it("date -> ISO string; rejects non-dates", () => {
    expect(serializeValue({ kind: "date", value: "2026-02-28" })).toEqual({ ok: true, value: "2026-02-28" });
    expect(serializeValue({ kind: "date", value: "2026-02-30" }).ok).toBe(false);
    expect(serializeValue({ kind: "date", value: "2026-Q3" }).ok).toBe(false);
    expect(isIsoDate("2024-02-29")).toBe(true);
  });

  it("text_list -> array of trimmed non-empty strings", () => {
    expect(serializeValue({ kind: "text_list", items: [" EMEA ", "", "APAC"] })).toEqual({ ok: true, value: ["EMEA", "APAC"] });
    expect(serializeValue({ kind: "text_list", items: [] }).ok).toBe(false);
  });

  it("number -> JSON number (not a string)", () => {
    const r = serializeValue({ kind: "number", value: " 1000.50 " });
    expect(r).toEqual({ ok: true, value: 1000.5 });
    expect(typeof (r as { value: unknown }).value).toBe("number");
    expect(serializeValue({ kind: "number", value: "-5" })).toEqual({ ok: true, value: -5 });
    expect(serializeValue({ kind: "number", value: "1e3" })).toEqual({ ok: true, value: 1000 });
    expect(serializeValue({ kind: "number", value: "" }).ok).toBe(false);
    expect(serializeValue({ kind: "number", value: "abc" }).ok).toBe(false);
    expect(serializeValue({ kind: "number", value: "Infinity" }).ok).toBe(false);
  });

  it("number accepts decimal/scientific notation only", () => {
    for (const bad of ["0x10", "0b11", "0o7", "1_000", "1,000", "+-1", "1e", ".", "NaN"])
      expect(serializeValue({ kind: "number", value: bad }).ok, bad).toBe(false);
    expect(serializeValue({ kind: "number", value: "+7" })).toEqual({ ok: true, value: 7 });
    expect(serializeValue({ kind: "number", value: ".5" })).toEqual({ ok: true, value: 0.5 });
    expect(serializeValue({ kind: "number", value: "2.50E-3" })).toEqual({ ok: true, value: 0.0025 });
    expect(serializeValue({ kind: "number", value: "-0" })).toEqual({ ok: true, value: -0 });
    expect(serializeValue({ kind: "number", value: "0.1" })).toEqual({ ok: true, value: 0.1 });
  });

  it("number refuses text a JSON double can't hold exactly", () => {
    for (const bad of ["9007199254740993", "12345678901234567890", "0.30000000000000001", "1e-400"]) {
      const r = serializeValue({ kind: "number", value: bad });
      expect(r.ok, bad).toBe(false);
      expect((r as { error: string }).error).toContain("precision");
    }
    expect(serializeValue({ kind: "number", value: "9007199254740992" })).toEqual({ ok: true, value: 9007199254740992 });
    expect(serializeValue({ kind: "number", value: "1e21" })).toEqual({ ok: true, value: 1e21 });
  });

  it("canonicalDecimal compares spellings by exact value", () => {
    expect(canonicalDecimal("1000.50")).toBe(canonicalDecimal("1.0005e3"));
    expect(canonicalDecimal("-0.000")).toBe("0");
    expect(canonicalDecimal("1e+21")).toBe(canonicalDecimal("1000000000000000000000"));
    expect(canonicalDecimal("0.30000000000000001")).not.toBe(canonicalDecimal("0.3"));
  });

  it("boolean -> true/false; text -> string; grain -> unit", () => {
    expect(serializeValue({ kind: "boolean", value: false })).toEqual({ ok: true, value: false });
    expect(serializeValue({ kind: "text", value: "a b" })).toEqual({ ok: true, value: "a b" });
    expect(serializeValue({ kind: "grain", value: "quarter" })).toEqual({ ok: true, value: "quarter" });
    expect(serializeValue({ kind: "grain", value: "hour" }).ok).toBe(false);
  });

  it("round-trips: serialized JSON matches the strict input format", () => {
    const json = JSON.stringify({
      p: (serializeValue({ kind: "date_range", start: "2026-01-01", end: "2026-02-01" }) as { value: unknown }).value,
      n: (serializeValue({ kind: "number", value: "42" }) as { value: unknown }).value,
      l: (serializeValue({ kind: "text_list", items: ["a"] }) as { value: unknown }).value,
    });
    expect(json).toBe('{"p":{"start":"2026-01-01","end":"2026-02-01"},"n":42,"l":["a"]}');
  });
});

describe("run values", () => {
  const cards = [
    card({ name: "period", type: "date_range", original_value: { start: "2026-01-01", end: "2026-04-01" } }),
    card({ name: "min_amount", type: "number", role: "fixed", original_value: 500 }),
    card({ name: "regions", type: "text_list", original_value: ["EMEA"] }),
  ];

  it("sends changeable values and skips fixed ones unless overridden", () => {
    const r = buildRunValues(cards, {}, new Set());
    expect(r.values).toEqual({ period: { start: "2026-01-01", end: "2026-04-01" }, regions: ["EMEA"] });
    expect(r.overridden).toEqual([]);
    expect(r.errors).toEqual({});
  });

  it("includes an overridden fixed variable", () => {
    const r = buildRunValues(cards, { min_amount: { kind: "number", value: "750" } }, new Set(["min_amount"]));
    expect(r.values.min_amount).toBe(750);
    expect(r.overridden).toEqual(["min_amount"]);
  });

  it("reports form errors per variable", () => {
    const r = buildRunValues(cards, { regions: { kind: "text_list", items: [] } }, new Set());
    expect(r.errors.regions).toMatch(/at least one/);
    expect(r.values.regions).toBeUndefined();
  });

  it("builds a /run request keyed by the definition alias", () => {
    const req = buildRunRequest({ id: "closed_deals", version: 3 }, { regions: ["EMEA"] }, { run: false, limit: 100 });
    expect(req).toEqual({
      uses: { closed_deals: { id: "closed_deals", version: 3 } },
      values: { closed_deals: { regions: ["EMEA"] } },
      run: false,
      limit: 100,
    });
    expect(buildRunRequest({ id: "x", version: 1 }, {}, { run: true, limit: 10, profile: "wh" }).profile).toBe("wh");
  });

  it("flags values outside the allowed list", () => {
    expect(outsideAllowed("EMEA", ["EMEA", "APAC"])).toBe(false);
    expect(outsideAllowed(["EMEA", "LATAM"], ["EMEA", "APAC"])).toBe(true);
    expect(outsideAllowed(5, [5, 10])).toBe(false);
    expect(outsideAllowed("x", null)).toBe(false);
  });
});
