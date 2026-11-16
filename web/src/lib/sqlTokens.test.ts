import { describe, expect, it } from "vitest";
import { tokenize, tokenizeLines } from "./sqlTokens";

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
