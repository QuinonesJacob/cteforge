import { describe, expect, it } from "vitest";
import type { JoinCard } from "../api/types";
import { addRequest, annotateDraft, annotateRequest, emptyAddDraft, lines, relationshipLabel, sideSummary } from "./joins";

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

  it("builds an add request and explains a bad form", () => {
    expect(addRequest(emptyAddDraft()).error).toBe("Name both tables.");
    const draft = { ...emptyAddDraft(), left_table: "a", right_table: "b" };
    expect(addRequest(draft).error).toBe("Add at least one key pair.");
    expect(addRequest({ ...draft, keys: [{ left: "x", right: "" }] }).error).toBe("Each key pair needs both columns.");
    expect(addRequest({ ...draft, keys: [{ left: " x ", right: "y" }], relationship: "many_to_one", traps: "t" }).request).toEqual({
      action: "add",
      left_table: "a",
      right_table: "b",
      keys: [{ left: "x", right: "y" }],
      join_type: "inner",
      relationship: "many_to_one",
      traps: ["t"],
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
});
