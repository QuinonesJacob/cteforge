import { describe, expect, it } from "vitest";
import { fieldLabel, listDelta, queryChangeLabel } from "./metadata";

describe("metadata changes (ADR 0029)", () => {
  it("labels fields and splits list changes into added / removed", () => {
    expect(fieldLabel("dont_use_when")).toBe("Don't use when");
    expect(fieldLabel("something_else")).toBe("something else");
    expect(listDelta(["a", "b"], ["b", "c"])).toEqual({ added: ["c"], removed: ["a"] });
    expect(listDelta(null, ["x"])).toEqual({ added: ["x"], removed: [] });
    expect(listDelta("old", "new")).toBeNull();
  });

  it("says metadata only when the query does not change", () => {
    expect(queryChangeLabel(4, 1)).toBe("+4 −1");
    expect(queryChangeLabel(0, 0)).toBe("metadata only");
    expect(queryChangeLabel(null, null)).toBeNull();
  });
});
