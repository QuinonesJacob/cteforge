import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { DefinitionCard } from "../api/types";
import { DefinitionOverview } from "./DefinitionOverview";

const composed: DefinitionCard = {
  id: "realization_with_booked",
  version: 1,
  title: "Realization next to booked",
  meaning: "",
  keywords: [],
  use_when: "",
  dont_use_when: "",
  source_dialect: "duckdb",
  state: "llm_annotated",
  join_keys: [],
  outputs: ["month", "region"],
  tables: ["deals"],
  template: "SELECT rr.month FROM rr JOIN bc ON rr.region = bc.region",
  variables: [{ name: "rr.period", type: "date_range", role: "changeable", input_format: "{start,end}", description: "", original_value: { start: "2026-01-01", end: "2026-04-01" } }],
  composed: true,
  composition: {
    parts: [
      { alias: "rr", id: "realization_rate", pinned_version: null, version: 3, title: "RR", state: "verified", latest_version: 3, superseded: false, usable: true, composed: false },
      { alias: "bc", id: "booked_vs_collected", pinned_version: 1, version: 1, title: "BC", state: "llm_annotated", latest_version: 2, superseded: true, usable: true, composed: false },
    ],
    fixed: { bc: { min_booked: 100 } },
    open_variables: ["rr.period"],
    select: "SELECT rr.month FROM rr JOIN bc ON rr.region = bc.region",
    compiled_sql: "WITH rr AS (SELECT 1) SELECT rr.month FROM rr",
    dialect: "duckdb",
    issues: [],
  },
  used_by: [{ definition_id: "big_combo", definition_version: 2, title: "Big", state: "draft", alias: "x", pinned_version: null }],
  joins: [
    {
      id: "deals__invoices__deal_id=deal_id",
      left_table: "deals",
      right_table: "invoices",
      keys: [{ left: "deal_id", right: "deal_id" }],
      join_type: "inner",
      usage_count: 1,
      traps: [],
      notes: "",
      sources: ["realization_rate"],
      known: true,
      confirmed: true,
      pending_proposals: [],
    },
  ],
};

describe("DefinitionOverview (ADR 0026)", () => {
  it("shows a composed definition's parts, fixed/open variables, compiled SQL, joins to and used by", () => {
    render(
      <MemoryRouter>
        <DefinitionOverview d={composed} />
      </MemoryRouter>,
    );
    const parts = screen.getByRole("heading", { name: "Parts" }).closest("section")!;
    expect(within(parts).getByRole("link", { name: "realization_rate" })).toHaveAttribute("href", "/definitions/realization_rate?version=3");
    expect(within(parts).getByText("latest (v3)")).toBeInTheDocument();
    expect(within(parts).getByText("pinned v1")).toBeInTheDocument();
    expect(within(parts).getByText("superseded (latest v2)")).toBeInTheDocument();
    expect(screen.getByText("bc.min_booked")).toBeInTheDocument();
    expect(screen.getAllByText("rr.period").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Compiled SQL" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "SQL template" })).not.toBeInTheDocument();
    const joins = screen.getByRole("region", { name: "Joins of realization_with_booked" });
    expect(within(joins).getByText("confirmed")).toBeInTheDocument();
    const usedBy = screen.getByRole("region", { name: "Used by" });
    expect(within(usedBy).getByRole("link", { name: "big_combo" })).toHaveAttribute("href", "/definitions/big_combo");
  });

  it("shows a SQL definition's template", () => {
    render(
      <MemoryRouter>
        <DefinitionOverview d={{ ...composed, composed: false, composition: null, joins: [], used_by: [] }} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "SQL template" })).toBeInTheDocument();
    expect(screen.getByText("No join-map edges touch its tables.")).toBeInTheDocument();
    expect(screen.getByText("No composed definitions use it.")).toBeInTheDocument();
  });
});
