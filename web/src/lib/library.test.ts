import { describe, expect, it } from "vitest";
import {
  REVIEW_QUEUE_HREF,
  clearedFilters,
  filterPills,
  filtersFromParams,
  isFiltered,
  libraryQuery,
  paramsFromFilters,
  pendingLabel,
  proposalKindLabel,
  rowHref,
  toggle,
} from "./library";

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
      sort: "relevance",
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

  it("sorts the review queue oldest first (ADR 0029)", () => {
    const f = filtersFromParams(new URLSearchParams(REVIEW_QUEUE_HREF.slice(2)));
    expect(f.needsMyReview).toBe(true);
    expect(f.sort).toBe("oldest");
    expect(libraryQuery(f)).toEqual({ needs_my_review: true, sort: "oldest" });
    expect(paramsFromFilters(f).toString()).toBe("review=mine&sort=oldest");
    expect(isFiltered(f)).toBe(true);
  });

  it("lists active filters as removable chips; clear all keeps the words", () => {
    const f = filtersFromParams(new URLSearchParams("q=rev&trust=verified&approval=pending&review=mine&sort=oldest"));
    const pills = filterPills(f);
    expect(pills.map((p) => `${p.group}: ${p.label}`)).toEqual([
      "Review: Needs my review",
      "Trust: Verified",
      "Approval: Pending",
      "Sort: Oldest first",
    ]);
    expect(pills[1]?.without.trust).toEqual([]);
    expect(filterPills(filtersFromParams(new URLSearchParams()))).toEqual([]);
    expect(filterPills(filtersFromParams(new URLSearchParams("approval=")))[0]?.label).toBe("Any");
    const cleared = clearedFilters(f);
    expect(cleared.q).toBe("rev");
    expect(isFiltered(cleared)).toBe(false);
    expect(proposalKindLabel("revise_definition")).toBe("Change");
  });
});
