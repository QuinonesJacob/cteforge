import { describe, expect, it } from "vitest";
import type { DefinitionCard, LiteralInfo } from "../api/types";
import { toAnnotation } from "./importBuilder";
import { changedMetadata, draftFromAnnotation, metadataFromCard, seedDrafts } from "./revise";

const card: DefinitionCard = {
  id: "rr",
  version: 3,
  title: "Realization rate",
  meaning: "Collected / booked",
  keywords: ["rr", "cash"],
  use_when: "cash questions",
  dont_use_when: "",
  source_dialect: "duckdb",
  state: "verified",
  owner: null,
  grain: "one row per month",
  join_keys: ["region"],
  outputs: ["region"],
  tables: ["deals"],
  variables: [],
  template: "SELECT 1",
};

const lit = (id: string): LiteralInfo => ({
  id,
  scope: id.split(".")[0] ?? "main",
  value: "x",
  is_string: true,
  form: "bare",
  operator: "=",
  column_sql: "c",
  column: null,
  wrapped: false,
  context: [],
});

describe("revise helpers", () => {
  it("round-trips a stored annotation through the draft unchanged", () => {
    const ann = {
      name: "regions",
      type: "text_list" as const,
      role: "changeable" as const,
      literals: ["main.L1", "main.L2"],
      column: "deals.region",
      description: "Regions",
      allowed: ["EMEA", "APAC"],
    };
    expect(toAnnotation(draftFromAnnotation(ann))).toEqual(ann);
    const num = { name: "min", type: "number" as const, role: "fixed" as const, literals: ["main.L3"], description: "", unit: "USD", allowed: [1, 2] };
    expect(toAnnotation(draftFromAnnotation(num))).toEqual(num);
  });

  it("sends only the metadata keys that changed", () => {
    const before = metadataFromCard(card);
    expect(before.owner).toBe("");
    expect(changedMetadata(before, before)).toEqual({});
    const after = { ...before, keywords: "rr, cash, realization", owner: "ana", summary: "ignored" };
    expect(changedMetadata(before, after)).toEqual({ keywords: ["rr", "cash", "realization"], owner: "ana" });
    expect(changedMetadata(before, { ...before, grain: "" })).toEqual({ grain: null });
  });

  it("seeds only drafts whose every ID exists in the parse report", () => {
    const keep = draftFromAnnotation({ name: "a", type: "text", role: "fixed", literals: ["main.L1"], description: "" });
    const grain = draftFromAnnotation({ name: "g", type: "grain", role: "fixed", literals: ["main.G1"], description: "" });
    const drop = draftFromAnnotation({ name: "b", type: "text", role: "fixed", literals: ["main.L1", "main.L9"], description: "" });
    const report = { literals: [lit("main.L1")], grains: [{ id: "main.G1" } as never] };
    const { kept, dropped } = seedDrafts([keep, grain, drop], report);
    expect(kept.map((d) => d.name)).toEqual(["a", "g"]);
    expect(dropped.map((d) => d.name)).toEqual(["b"]);
  });
});
