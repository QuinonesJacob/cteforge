import { describe, expect, it } from "vitest";
import { markValues, tokenize, tokenizeLines } from "./sqlTokens";

const kinds = (sql: string) => tokenize(sql).filter((t) => t.text.trim()).map((t) => [t.kind, t.text.trim()]);

describe("sql tokenizer (display only)", () => {
  it("finds keywords, strings, numbers, placeholders", () => {
    expect(kinds("SELECT a FROM t WHERE d >= $period__start AND n > 1.5 AND s = 'it''s'")).toEqual([
      ["keyword", "SELECT"],
      ["text", "a"],
      ["keyword", "FROM"],
      ["text", "t"],
      ["keyword", "WHERE"],
      ["text", "d"],
      ["operator", ">="],
      ["placeholder", "$period__start"],
      ["keyword", "AND"],
      ["text", "n"],
      ["operator", ">"],
      ["number", "1.5"],
      ["keyword", "AND"],
      ["text", "s"],
      ["operator", "="],
      ["string", "'it''s'"],
    ]);
  });

  it("treats :name as a placeholder but not ::casts", () => {
    expect(kinds("x::date = :day")).toEqual([
      ["text", "x"],
      ["operator", "::"],
      ["keyword", "date"],
      ["operator", "="],
      ["placeholder", ":day"],
    ]);
  });

  it("keeps comments and splits multi-line tokens into lines", () => {
    const lines = tokenizeLines("/* a\nb */ SELECT 1 -- c\nFROM t");
    expect(lines).toHaveLength(3);
    expect(lines[0]?.[0]).toEqual({ kind: "comment", text: "/* a" });
    expect(lines[1]?.[0]).toEqual({ kind: "comment", text: "b */" });
    expect(lines[2]?.[0]).toEqual({ kind: "keyword", text: "FROM" });
  });

  it("never loses text", () => {
    const sql = "WITH x AS (SELECT \"Col\" FROM `t` WHERE y IN ('a','b')) SELECT * FROM x;\n";
    expect(tokenize(sql).map((t) => t.text).join("")).toBe(sql);
  });
});

describe("markValues (Import, ADR 0029)", () => {
  it("marks literal values: variables, selected, else plain values", () => {
    const lines = tokenizeLines("select * from t\nwhere d >= '2026-01-01' and s <> 'void' and n > 5 and x = 'other'");
    const lits = [
      { id: "q.L1", value: "2026-01-01", is_string: true },
      { id: "q.L2", value: "void", is_string: true },
      { id: "q.L3", value: "5", is_string: false },
    ];
    const marked = markValues(lines, lits, new Set(["q.L1"]), new Set(["q.L3"]));
    const marks = marked.flat().filter((t) => t.mark).map((t) => `${t.text}:${t.mark}`);
    expect(marks).toEqual(["'2026-01-01':variable", "'void':value", "5:selected"]);
  });
});
