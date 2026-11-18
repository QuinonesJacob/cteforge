import { describe, expect, it } from "vitest";
import { filtersFromParams, isFiltered, libraryQuery, paramsFromFilters, pendingLabel, rowHref, toggle } from "./library";

describe("library filters", () => {
  it("defaults to pending + approved, hiding rejected and withdrawn", () => {
    const f = filtersFromParams(new URLSearchParams(""));
    expect(f.approval).toEqual(["pending", "approved"]);
    expect(libraryQuery(f)).toEqual({});
    expect(paramsFromFilters(f).toString()).toBe("");
    expect(isFiltered(f)).toBe(false);
  });

  it("round-trips URL params and builds the server query", () => {
    const f = filtersFromParams(new URLSearchParams("q=rev&trust=verified,draft&approval=rejected&created=agent&verified=human&review=mine"));
    expect(f).toEqual({
      q: "rev",
      trust: ["verified", "draft"],
      approval: ["rejected"],
      createdBy: ["agent"],
      verifiedBy: ["human"],
      needsMyReview: true,
    });
    expect(libraryQuery(f)).toEqual({
      q: "rev",
      trust: ["verified", "draft"],
      approval: ["rejected"],
      created_by: ["agent"],
      verified_by: ["human"],
      needs_my_review: true,
    });
    expect(filtersFromParams(paramsFromFilters(f))).toEqual(f);
    expect(isFiltered(f)).toBe(true);
  });

  it("treats no approval selected as every status, and ignores unknown values", () => {
    const f = filtersFromParams(new URLSearchParams("approval=&trust=bogus"));
    expect(f.approval).toEqual([]);
    expect(f.trust).toEqual([]);
    expect(libraryQuery(f).approval).toEqual(["pending", "approved", "rejected", "withdrawn"]);
  });

  it("toggles values", () => {
    expect(toggle(["a"], "b", true)).toEqual(["a", "b"]);
    expect(toggle(["a", "b"], "a", false)).toEqual(["b"]);
    expect(toggle(["a"], "a", true)).toEqual(["a"]);
  });

  it("labels pending proposals and links rows", () => {
    expect(pendingLabel({ kind: "revise_definition" })).toBe("Revision pending");
    expect(pendingLabel({ kind: "rename_definition" })).toBe("Rename pending");
    const base = { title: "x", meaning: "", composed: false, approval: "pending" as const, pending: [], needs_my_review: false };
    expect(rowHref({ ...base, id: "rr", version: 2 })).toBe("/definitions/rr");
    expect(
      rowHref({
        ...base,
        id: "new",
        version: null,
        latest_proposal: { id: "p1", kind: "new_definition", status: "pending", author: "a", author_kind: "agent", created_at: "", summary: "" },
      }),
    ).toBe("/review/p1");
  });
});
