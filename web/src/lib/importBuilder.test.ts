import { describe, expect, it } from "vitest";
import type { GrainCandidate, LiteralInfo, RangeCandidate } from "../api/types";
import {
  allPairsSuggested,
  carryOverDrafts,
  connectionDialectHint,
  defaultImportDialect,
  descriptionRule,
  grainUsable,
  importConnection,
  newGrainDraft,
  rangeShape,
  sameUnitGrains,
  isIdent,
  newDraft,
  orderRangeLiterals,
  parseAllowed,
  slugify,
  suggestName,
  suggestType,
  toAnnotation,
  toMetadata,
  typeFamily,
  validateDraft,
  validateMetadata,
  emptyMetadata,
  type VariableDraft,
} from "./importBuilder";

function lit(partial: Partial<LiteralInfo> & Pick<LiteralInfo, "id" | "value">): LiteralInfo {
  return {
    scope: "main",
    is_string: true,
    form: "bare",
    operator: "=",
    column_sql: "t.col",
    column: { table: "t", column: "col" },
    wrapped: false,
    context: [],
    ...partial,
  };
}

const start = lit({ id: "q.L1", value: "2026-01-01", operator: ">=", compared_type: "DATE", column_sql: "d.closed_at", column: { table: "d", column: "closed_at" } });
const end = lit({ id: "q.L2", value: "2026-04-01", operator: "<", compared_type: "DATE", column_sql: "d.closed_at", column: { table: "d", column: "closed_at" } });
const ranges: RangeCandidate[] = [{ scope: "main", column_sql: "d.closed_at", start: "q.L1", end: "q.L2", shape: "half_open" }];

describe("typeFamily", () => {
  it.each([
    ["DATE", "date"],
    ["date", "date"],
    ["TIMESTAMP_NTZ", "timestamp"],
    ["DATETIME", "timestamp"],
    ["TIMESTAMP WITH TIME ZONE", "timestamp"],
    ["DECIMAL(18,2)", "number"],
    ["BIGINT", "number"],
    ["DOUBLE", "number"],
    ["NUMBER(38,0)", "number"],
    ["BOOLEAN", "boolean"],
    ["VARCHAR", "text"],
    ["STRING", "text"],
    ["STRUCT<a INT>", "number"],
  ])("%s -> %s", (input, expected) => {
    // STRUCT<a INT> contains INT; documented: coarse family only.
    expect(typeFamily(input)).toBe(expected);
  });

  it("returns null when unknown", () => {
    expect(typeFamily(null)).toBeNull();
    expect(typeFamily("GEOGRAPHY")).toBeNull();
  });
});

describe("suggestType", () => {
  it("suggested range pair -> date_range", () => {
    expect(suggestType([start, end], ranges)).toBe("date_range");
    expect(suggestType([end, start], ranges)).toBe("date_range");
  });

  it("from the compared type", () => {
    expect(suggestType([start])).toBe("date");
    expect(suggestType([lit({ id: "a", value: "2026-01-01 10:00:00", compared_type: "TIMESTAMP" })])).toBe("date");
    expect(suggestType([lit({ id: "a", value: "500", is_string: false, compared_type: "DOUBLE" })])).toBe("number");
    expect(suggestType([lit({ id: "a", value: "won", compared_type: "VARCHAR" })])).toBe("text");
    expect(suggestType([lit({ id: "a", value: "true", is_string: false, compared_type: "BOOLEAN" })])).toBe("boolean");
  });

  it("compared type wins over the literal's look (string number on a numeric expression)", () => {
    expect(suggestType([lit({ id: "a", value: "500", is_string: true, compared_type: "DOUBLE" })])).toBe("number");
  });

  it("IN list literals -> text_list", () => {
    const a = lit({ id: "a", value: "EMEA", operator: "IN", in_list: "g1", compared_type: "VARCHAR" });
    const b = lit({ id: "b", value: "APAC", operator: "IN", in_list: "g1", compared_type: "VARCHAR" });
    expect(suggestType([a, b])).toBe("text_list");
    expect(suggestType([a])).toBe("text_list");
  });

  it("falls back to the literal text when the type is unknown", () => {
    expect(suggestType([lit({ id: "a", value: "12.5", is_string: false })])).toBe("number");
    expect(suggestType([lit({ id: "a", value: "2026-03-01" })])).toBe("date");
    expect(suggestType([lit({ id: "a", value: "TRUE", is_string: false })])).toBe("boolean");
    expect(suggestType([lit({ id: "a", value: "hello" })])).toBe("text");
  });

  it("unpaired date bounds -> date_range (analyst may still choose)", () => {
    const lo = lit({ id: "x1", value: "2026-01-01", operator: ">=", compared_type: "DATE" });
    const hi = lit({ id: "x2", value: "2026-02-01", operator: "<=", compared_type: "DATE" });
    expect(suggestType([lo, hi], [])).toBe("date_range");
  });

  it("literals never suggest grain (grain variables are built from grain candidates)", () => {
    expect(suggestType([lit({ id: "g", value: "month" })], [])).toBe("text");
  });
});

describe("range ordering", () => {
  const byId = { [start.id]: start, [end.id]: end };
  it("orders a suggested pair as start, end", () => {
    expect(orderRangeLiterals(["q.L2", "q.L1"], ranges, byId)).toEqual(["q.L1", "q.L2"]);
    expect(allPairsSuggested(["q.L1", "q.L2"], ranges)).toBe(true);
    expect(allPairsSuggested(["q.L2", "q.L1"], ranges)).toBe(false);
  });

  it("orders unpaired bounds by operator", () => {
    const hi = lit({ id: "h", value: "2026-02-01", operator: "<=" });
    const lo = lit({ id: "l", value: "2026-01-01", operator: ">" });
    expect(orderRangeLiterals(["h", "l"], [], { h: hi, l: lo })).toEqual(["l", "h"]);
    expect(allPairsSuggested(["l", "h"], [])).toBe(false);
  });
});

describe("descriptionRule (ADR 0015)", () => {
  it("not required for a fixed variable even in an OR branch", () => {
    const l = lit({ id: "o", value: "x", context: ["or_branch"] });
    expect(descriptionRule({ type: "text", role: "fixed", literals: ["o"] }, { o: l }, []).required).toBe(false);
  });

  it.each(["negated", "or_branch", "case_condition", "outer_join_on"] as const)(
    "required for a changeable variable with %s context",
    (ctx) => {
      const l = lit({ id: "o", value: "x", context: [ctx] });
      const rule = descriptionRule({ type: "text", role: "changeable", literals: ["o"] }, { o: l }, []);
      expect(rule.required).toBe(true);
      expect(rule.reasons[0]).toContain("o is");
    },
  );

  it.each(["join_on", "having", "subquery", "value_wrapped"] as const)("not required for %s alone", (ctx) => {
    const l = lit({ id: "o", value: "x", context: [ctx] });
    expect(descriptionRule({ type: "text", role: "changeable", literals: ["o"] }, { o: l }, []).required).toBe(false);
  });

  it("required for a changeable date_range that is not a suggested pair", () => {
    const rule = descriptionRule({ type: "date_range", role: "changeable", literals: ["a", "b"] }, {}, ranges);
    expect(rule.required).toBe(true);
    expect(rule.reasons.join()).toContain("range_not_simple");
    expect(descriptionRule({ type: "date_range", role: "changeable", literals: ["q.L1", "q.L2"] }, {}, ranges).required).toBe(false);
  });

  it("not required for a fixed date_range, even when not a suggested pair (backend: role != changeable)", () => {
    expect(descriptionRule({ type: "date_range", role: "fixed", literals: ["a", "b"] }, {}, ranges).required).toBe(false);
    const d: VariableDraft = { name: "p", type: "date_range", role: "fixed", literals: ["a", "b"], column: "", description: "", allowed: "", unit: "", format: "" };
    expect(validateDraft(d, {}, ranges, [])).toEqual([]);
  });

  it("required when suggested pairs have different shapes (no shape recorded)", () => {
    const mixed: RangeCandidate[] = [
      ...ranges,
      { scope: "main", column_sql: "d.created_at", start: "q.L3", end: "q.L4", shape: "inclusive" },
    ];
    expect(rangeShape(["q.L1", "q.L2", "q.L3", "q.L4"], mixed)).toBeNull();
    const rule = descriptionRule({ type: "date_range", role: "changeable", literals: ["q.L1", "q.L2", "q.L3", "q.L4"] }, {}, mixed);
    expect(rule.required).toBe(true);
    expect(rule.reasons.join()).toContain("different shapes");
    const same: RangeCandidate[] = [...ranges, { ...mixed[1]!, shape: "half_open" }];
    expect(rangeShape(["q.L1", "q.L2", "q.L3", "q.L4"], same)).toBe("half_open");
    expect(descriptionRule({ type: "date_range", role: "changeable", literals: ["q.L1", "q.L2", "q.L3", "q.L4"] }, {}, same).required).toBe(false);
  });

  it("uses grain contexts for a changeable grain variable", () => {
    const g: GrainCandidate = { id: "main.G1", scope: "main", unit: "month", column_sql: "d.closed_at", sql: "DATE_TRUNC('month', d.closed_at)", context: ["case_condition"] };
    const draft = { type: "grain" as const, role: "changeable" as const, literals: ["main.G1"] };
    expect(descriptionRule(draft, {}, [], { "main.G1": g }).required).toBe(true);
    expect(descriptionRule({ ...draft, role: "fixed" }, {}, [], { "main.G1": g }).required).toBe(false);
  });
});

describe("drafts", () => {
  it("newDraft suggests type, name, order, column and format; role fixed by default", () => {
    const d = newDraft([end, start], ranges, []);
    expect(d).toMatchObject({ type: "date_range", role: "fixed", literals: ["q.L1", "q.L2"], name: "closed_period", column: "d.closed_at" });
    const f = newDraft([lit({ id: "s", value: "01-15-2026", suggested_format: "%m-%d-%Y" })], [], []);
    expect(f.format).toBe("%m-%d-%Y");
  });

  it("suggestName is a unique identifier", () => {
    const l = lit({ id: "a", value: "x", column: null, column_sql: "LOWER(a.Region)" });
    expect(suggestName([l], "text")).toBe("region");
    expect(suggestName([l], "text", ["region"])).toBe("region_2");
    expect(isIdent("region_2")).toBe(true);
    expect(isIdent("bad__name")).toBe(false);
    expect(isIdent("2bad")).toBe(false);
    expect(slugify("Closed deals — by Region (Q3)")).toBe("closed_deals_by_region_q3");
  });

  const base: VariableDraft = {
    name: "status",
    type: "text",
    role: "changeable",
    literals: ["o"],
    column: "",
    description: "",
    allowed: "",
    unit: "",
    format: "",
  };

  it("validateDraft blocks missing required descriptions, bad names, duplicates, odd range arity", () => {
    const l = lit({ id: "o", value: "x", context: ["negated"] });
    expect(validateDraft(base, { o: l }, [], [])).toEqual(["A description is required for this variable (see why below)."]);
    expect(validateDraft({ ...base, description: "Excludes status" }, { o: l }, [], [])).toEqual([]);
    expect(validateDraft({ ...base, name: "Bad Name", description: "d" }, { o: l }, [], [])).toHaveLength(1);
    expect(validateDraft({ ...base, description: "d" }, { o: l }, [], ["status"])).toHaveLength(1);
    expect(validateDraft({ ...base, type: "date_range", literals: ["q.L1"], description: "d" }, {}, ranges, [])).toContain(
      "A date range needs a start and an end literal (pairs).",
    );
  });

  it("toAnnotation keeps only fields that apply to the type", () => {
    expect(toAnnotation({ ...base, type: "number", unit: "TFLOPS", format: "%Y", allowed: "100, 200" })).toEqual({
      name: "status",
      type: "number",
      role: "changeable",
      literals: ["o"],
      description: "",
      unit: "TFLOPS",
      allowed: [100, 200],
    });
    expect(toAnnotation({ ...base, type: "date", format: "%m-%d-%Y", unit: "x", column: " d.x " })).toMatchObject({
      format: "%m-%d-%Y",
      column: "d.x",
    });
    expect(toAnnotation({ ...base, type: "date", unit: "x" }).unit).toBeUndefined();
    expect(parseAllowed("a, b,\nc", "text")).toEqual(["a", "b", "c"]);
    expect(parseAllowed("true,false", "boolean")).toEqual([true, false]);
    expect(parseAllowed(" ", "text")).toBeNull();
  });
});

describe("grain variables (M1.5)", () => {
  const g = (id: string, unit: string): GrainCandidate => ({
    id,
    scope: "main",
    unit,
    column_sql: "d.closed_at",
    sql: `DATE_TRUNC('${unit}', d.closed_at)`,
    context: [],
  });
  const grains = [g("main.G1", "month"), g("main.G2", "month"), g("main.G3", "hour")];
  const grainsById = Object.fromEntries(grains.map((x) => [x.id, x]));

  it("only day…year truncations can become a grain variable", () => {
    expect(grainUsable(grains[0]!)).toBe(true);
    expect(grainUsable(grains[2]!)).toBe(false);
    expect(grainUsable(g("x", "column cfg.month"))).toBe(false);
    expect(sameUnitGrains(grains, grains[0]!).map((x) => x.id)).toEqual(["main.G1", "main.G2"]);
  });

  it("newGrainDraft names grain IDs, not literals", () => {
    const d = newGrainDraft([grains[0]!, grains[1]!], ["grain"]);
    expect(d).toMatchObject({ type: "grain", role: "fixed", name: "grain_2", literals: ["main.G1", "main.G2"], column: "d.closed_at" });
    expect(validateDraft(d, {}, [], [], grainsById)).toEqual([]);
    expect(toAnnotation(d)).toEqual({ name: "grain_2", type: "grain", role: "fixed", literals: ["main.G1", "main.G2"], description: "", column: "d.closed_at" });
  });

  it("validateDraft refuses mixing grains and literals, and mixed units", () => {
    const d = newGrainDraft([grains[0]!], []);
    expect(validateDraft({ ...d, literals: ["main.G1", "q.L1"] }, { "q.L1": start }, [], [], grainsById).join()).toContain("truncations");
    expect(validateDraft({ ...d, type: "text" }, {}, [], [], grainsById).join()).toContain("only become a grain");
    expect(validateDraft({ ...d, literals: ["main.G1", "main.G3"] }, {}, [], [], grainsById).join()).toContain("same unit");
  });
});

describe("carryOverDrafts (re-parse)", () => {
  const draft = (literals: string[]): VariableDraft => ({ name: literals.join("_"), type: "text", role: "fixed", literals, column: "", description: "", allowed: "", unit: "", format: "" });
  const region = lit({ id: "main.L1", value: "EMEA", column_sql: "d.region" });
  const amount = lit({ id: "main.L2", value: "100", is_string: false, operator: ">", column_sql: "d.amount" });

  it("keeps drafts whose IDs still name the same literals", () => {
    const r = { literals: [region, amount] };
    const { kept, dropped } = carryOverDrafts([draft(["main.L1"])], r, { literals: [{ ...region }, { ...amount }] });
    expect(kept).toHaveLength(1);
    expect(dropped).toHaveLength(0);
  });

  it("drops a draft whose positional ID now points at another literal", () => {
    // `status = 'open' AND` inserted before the region filter: main.L1 is now 'open'.
    const after = {
      literals: [
        lit({ id: "main.L1", value: "open", column_sql: "d.status" }),
        { ...region, id: "main.L2" },
        { ...amount, id: "main.L3" },
      ],
    };
    const { kept, dropped } = carryOverDrafts([draft(["main.L1"])], { literals: [region, amount] }, after);
    expect(kept).toEqual([]);
    expect(dropped.map((d) => d.name)).toEqual(["main.L1"]);
  });

  it("compares grain candidates too, and drops everything without a previous report", () => {
    const g1: GrainCandidate = { id: "main.G1", scope: "main", unit: "month", column_sql: "x", sql: "DATE_TRUNC('month', x)", context: [] };
    const before = { literals: [], grains: [g1] };
    expect(carryOverDrafts([draft(["main.G1"])], before, { literals: [], grains: [{ ...g1, unit: "week" }] }).kept).toEqual([]);
    expect(carryOverDrafts([draft(["main.G1"])], before, { literals: [], grains: [{ ...g1 }] }).kept).toHaveLength(1);
    expect(carryOverDrafts([draft(["main.G1"])], null, before).kept).toEqual([]);
  });
});

describe("metadata", () => {
  it("validates and converts", () => {
    const m = { ...emptyMetadata("jq"), title: "Closed deals", id: "closed_deals", meaning: "One row per deal", keywords: "won, bookings", join_keys: "account_id" };
    expect(validateMetadata(m)).toEqual({});
    expect(Object.keys(validateMetadata(emptyMetadata()))).toEqual(["title", "id", "meaning"]);
    expect(toMetadata(m)).toEqual({
      id: "closed_deals",
      title: "Closed deals",
      meaning: "One row per deal",
      keywords: ["won", "bookings"],
      use_when: "",
      dont_use_when: "",
      grain: null,
      join_keys: ["account_id"],
      owner: "jq",
    });
  });
});

describe("import connection and dialect", () => {
  const profiles = [
    { name: "wh", dialect: "duckdb" as const, default: false },
    { name: "sf", dialect: "snowflake" as const, default: true },
  ];
  it("uses the chosen profile, else the default connection, else the only profile", () => {
    expect(importConnection(profiles, "wh")?.name).toBe("wh");
    expect(importConnection(profiles, "")?.name).toBe("sf");
    expect(importConnection([{ name: "pg", dialect: "postgres" }], "")?.name).toBe("pg");
    expect(importConnection(profiles, "gone")).toBeUndefined();
  });
  it("takes the dialect from the connection", () => {
    expect(defaultImportDialect(profiles, "wh")).toEqual({ dialect: "duckdb", connection: profiles[0] });
    expect(defaultImportDialect(profiles, "")).toEqual({ dialect: "snowflake", connection: profiles[1] });
  });
  it("uses duckdb when no connection applies", () => {
    expect(defaultImportDialect([], "")).toEqual({ dialect: "duckdb", connection: undefined });
    expect(defaultImportDialect(null, "")).toEqual({ dialect: "duckdb", connection: undefined });
    const noDefault = profiles.map((p) => ({ ...p, default: false }));
    expect(defaultImportDialect(noDefault, "")).toEqual({ dialect: "duckdb", connection: undefined });
  });
  it("hints where the dialect came from, or that it differs", () => {
    expect(connectionDialectHint("snowflake", profiles[1])).toBe("From connection sf.");
    expect(connectionDialectHint("postgres", profiles[1])).toBe(
      "Differs from connection sf: checks run on SQL translated to snowflake, lower trust.",
    );
    expect(connectionDialectHint("duckdb", undefined)).toBeUndefined();
  });
});
