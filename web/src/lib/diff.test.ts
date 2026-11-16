import { describe, expect, it } from "vitest";
import { classifyDiffLine, diffStats, parseUnifiedDiff } from "./diff";

describe("classifyDiffLine", () => {
  it.each([
    ["+++ b/def.yml", "file"],
    ["--- a/def.yml", "file"],
    ["@@ -1,3 +1,4 @@", "hunk"],
    ["+  WHERE x = 1", "add"],
    ["-  WHERE x = 2", "remove"],
    ["   SELECT 1", "context"],
    ["", "context"],
    ["\\ No newline at end of file", "meta"],
    ["diff --git a/x b/x", "meta"],
  ])("%j -> %s", (line, kind) => {
    expect(classifyDiffLine(line)).toBe(kind);
  });
});

describe("parseUnifiedDiff", () => {
  const diff = [
    "--- a/closed_deals@1",
    "+++ b/closed_deals@2",
    "@@ -1,4 +1,4 @@",
    " SELECT *",
    "-WHERE amount > 100",
    "--- removed SQL comment",
    "+WHERE amount > 500",
    "+-- added SQL comment",
    " AND 1=1",
    "",
  ].join("\n");

  it("numbers old/new lines and classifies hunk bodies by first char", () => {
    const lines = parseUnifiedDiff(diff);
    expect(lines.map((l) => l.kind)).toEqual(["file", "file", "hunk", "context", "remove", "remove", "add", "add", "context"]);
    expect(lines[3]).toMatchObject({ oldNo: 1, newNo: 1 });
    expect(lines[4]).toMatchObject({ oldNo: 2, newNo: null });
    expect(lines[5]).toMatchObject({ kind: "remove", oldNo: 3, text: "--- removed SQL comment" });
    expect(lines[6]).toMatchObject({ oldNo: null, newNo: 2 });
    expect(lines[8]).toMatchObject({ oldNo: 4, newNo: 4 });
    expect(diffStats(lines)).toEqual({ added: 2, removed: 2 });
  });

  it("handles hunks without counts and CRLF", () => {
    const lines = parseUnifiedDiff("@@ -5 +5 @@\r\n-a\r\n+b\r\n");
    expect(lines.map((l) => [l.kind, l.oldNo, l.newNo])).toEqual([
      ["hunk", null, null],
      ["remove", 5, null],
      ["add", null, 5],
    ]);
  });

  it("returns nothing for an empty diff", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });
});
