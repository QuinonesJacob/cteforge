import { describe, expect, it } from "vitest";
import type { JoinCard } from "../api/types";
import {
  annotateDraft,
  annotateRequest,
  joinSides,
  lines,
  locationLabel,
  matchesJoin,
  relationshipLabel,
  sideSummary,
  sortJoins,
  trustMix,
  trustMixLabel,
  typeVariants,
} from "./joins";

const edge: JoinCard = {
  id: "deals__invoices__deal_id=deal_id",
  left_table: "deals",
  right_table: "invoices",
  keys: [{ left: "deal_id", right: "deal_id" }],
  join_type: "inner",
  relationship: null,
  usage_count: 2,
  traps: ["fan-out"],
  notes: "",
  sources: ["rr"],
  trusted_sources: ["rr"],
  known: true,
  confirmed: false,
  pending_proposals: [],
};

describe("join map forms", () => {
  it("annotates only what changed", () => {
    const draft = annotateDraft(edge);
    expect(annotateRequest(edge, draft)).toBeNull();
    expect(annotateRequest(edge, { ...draft, relationship: "one_to_many", summary: " why " })).toEqual({
      action: "annotate",
      id: edge.id,
      relationship: "one_to_many",
      summary: "why",
    });
    expect(annotateRequest(edge, { ...draft, traps: "fan-out\n\n double counts \n", notes: "see wiki" })).toEqual({
      action: "annotate",
      id: edge.id,
      traps: ["fan-out", "double counts"],
      notes: "see wiki",
      summary: "",
    });
  });

  it("formats relationships and evidence", () => {
    expect(lines(" a \n\nb")).toEqual(["a", "b"]);
    expect(relationshipLabel("one_to_many")).toBe("one to many");
    expect(relationshipLabel(null)).toBe("unknown");
    expect(sideSummary({ table: "deals", keys: ["deal_id"], rows: 10, distinct_keys: 10 })).toBe(
      "deals: 10 rows, 10 distinct keys (one)",
    );
  });

  it("puts the many side first from the relationship; unknown keeps left → right", () => {
    expect(joinSides({ ...edge, relationship: "one_to_many" })).toEqual({
      kind: "many_one",
      first: "invoices",
      second: "deals",
      keys: "deal_id = deal_id",
    });
    const e2 = { ...edge, left_table: "refunds", right_table: "invoices", keys: [{ left: "invoice_id", right: "id" }] };
    expect(joinSides({ ...e2, relationship: "many_to_one" })).toMatchObject({ kind: "many_one", first: "refunds", keys: "invoice_id = id" });
    expect(joinSides({ ...e2, relationship: "one_to_many" })).toMatchObject({ first: "invoices", second: "refunds", keys: "id = invoice_id" });
    expect(joinSides(e2).kind).toBe("unknown");
  });

  it("counts join-type variants per source definition, most used first", () => {
    const e = {
      ...edge,
      source_joins: [
        { definition: "a", join_type: "left", location: "cte" as const, scope: "x" },
        { definition: "a", join_type: "left", location: "main" as const },
        { definition: "b", join_type: "LEFT", location: "main" as const },
        { definition: "c", join_type: "inner", location: "subquery" as const },
      ],
    };
    expect(typeVariants(e)).toEqual([
      { type: "left", count: 2 },
      { type: "inner", count: 1 },
    ]);
    expect(typeVariants(edge)).toEqual([{ type: "inner", count: 2 }]);
  });

  it("summarizes the sources' trust and where each joins", () => {
    const mix = trustMix({ sources: ["a", "b", "c", "d"], source_states: { a: "verified", b: "verified", c: "draft" } });
    expect(mix).toEqual({ verified: 2, llm_annotated: 0, draft: 1 });
    expect(trustMixLabel(mix)).toBe("2 verified, 1 draft");
    expect(trustMixLabel({ verified: 0, llm_annotated: 0, draft: 0 })).toBe("no source definitions");
    expect(locationLabel({ location: "cte", scope: "monthly" })).toBe("CTE monthly");
    expect(locationLabel({ location: "main" })).toBe("main query");
    expect(locationLabel({ location: "subquery", scope: "m" })).toBe("subquery in CTE m");
    expect(locationLabel({ location: "subquery", scope: null })).toBe("subquery");
  });

  it("searches and sorts the list", () => {
    const other: JoinCard = { ...edge, id: "a__b", left_table: "accounts", right_table: "regions", usage_count: 5, sources: [], source_titles: {} };
    const withTitle = { ...edge, source_titles: { rr: "Realization rate" } };
    expect(matchesJoin(withTitle, "realization deals")).toBe(true);
    expect(matchesJoin(withTitle, "regions")).toBe(false);
    expect(matchesJoin(other, "")).toBe(true);
    expect(sortJoins([edge, other], "used_by", "desc").map((e) => e.id)).toEqual(["a__b", edge.id]);
    expect(sortJoins([edge, other], "first", "asc").map((e) => e.id)).toEqual(["a__b", edge.id]);
  });
});
