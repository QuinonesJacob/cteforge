import { describe, expect, it } from "vitest";
import type { DefinitionReferences } from "../api/types";
import { checkRename, partUse, referenceCount } from "./rename";

describe("checkRename", () => {
  it("needs a valid, different ID typed twice", () => {
    expect(checkRename("rr", "", "")).toEqual({ ready: false, idError: null });
    expect(checkRename("rr", "rr_monthly", "")).toEqual({ ready: false, idError: null });
    expect(checkRename("rr", "rr_monthly", "rr_month")).toEqual({ ready: false, idError: null });
    expect(checkRename("rr", "rr_monthly", "rr_monthly")).toEqual({ ready: true, idError: null });
  });

  it("explains a bad or unchanged ID", () => {
    expect(checkRename("rr", "Bad Id", "Bad Id").ready).toBe(false);
    expect(checkRename("rr", "Bad Id", "").idError).toMatch(/lower_snake_case/);
    expect(checkRename("rr", "a__b", "a__b").idError).toMatch(/lower_snake_case/);
    expect(checkRename("rr", "rr", "rr")).toEqual({ ready: false, idError: "That is the current ID." });
  });
});

describe("references", () => {
  it("counts composed definitions and proposals and words the version a part uses", () => {
    const refs = {
      id: "rr",
      version: 2,
      state: "llm_annotated",
      used_by: [{ definition_id: "a", definition_version: 1, title: "A", state: "llm_annotated", alias: "x", pinned_version: null }],
      proposals: [],
      former_ids: [],
      outside_warning: "",
    } satisfies DefinitionReferences;
    expect(referenceCount(refs)).toBe(1);
    expect(referenceCount(undefined)).toBe(0);
    expect(partUse(null)).toBe("follows the latest version");
    expect(partUse(3)).toBe("pins v3");
  });
});
