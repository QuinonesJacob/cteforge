import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DefinitionCard } from "../api/types";
import { AuthProvider } from "../state/AuthContext";
import { mockApi } from "../test/fetchMock";
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
      trusted_sources: ["realization_rate"],
      known: true,
      confirmed: true,
      pending_proposals: [],
    },
  ],
};

function renderOverview(d: DefinitionCard) {
  mockApi({ "GET /me": () => ({ body: { name: "ann", role: "admin", kind: "human", display_name: "Ann Lee" } }) });
  render(
    <MemoryRouter>
      <AuthProvider>
        <DefinitionOverview d={d} />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("DefinitionOverview (ADR 0026, 0029)", () => {
  it("shows a composed definition's parts, fixed/open variables, compiled SQL, joins and used by", async () => {
    renderOverview(composed);
    const parts = (await screen.findByRole("heading", { name: "Parts" })).closest("section")!;
    expect(within(parts).getByRole("link", { name: "realization_rate" })).toHaveAttribute("href", "/definitions/realization_rate?version=3");
    expect(within(parts).getByText("latest (v3)")).toBeInTheDocument();
    expect(within(parts).getByText("pinned v1")).toBeInTheDocument();
    expect(within(parts).getByText("superseded (latest v2)")).toBeInTheDocument();
    expect(screen.getByText("bc.min_booked")).toBeInTheDocument();
    expect(screen.getAllByText("rr.period").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Compiled SQL" })).toBeInTheDocument();
    expect(screen.queryByText(/Trusted SQL ·/)).not.toBeInTheDocument();
    const joins = screen.getByRole("list", { name: "Joins of realization_with_booked" });
    expect(within(joins).getByText("confirmed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See in Join map" })).toHaveAttribute("href", "/joins?definition=realization_with_booked");
    expect(screen.getByRole("link", { name: "big_combo" })).toHaveAttribute("href", "/definitions/big_combo");
  });

  it("shows use when / don't use when, variables with their original value, fixed ones marked, and the trusted SQL", async () => {
    renderOverview({
      ...composed,
      composed: false,
      composition: null,
      joins: [],
      used_by: [],
      owner: "ann",
      use_when: "- Monthly reporting\n- Comparing regions",
      dont_use_when: "Daily numbers",
      variables: [
        { name: "period", type: "date_range", role: "changeable", input_format: "{start,end}", description: "", original_value: { start: "2026-01-01", end: "2026-04-01" }, allowed: null },
        { name: "status", type: "text", role: "fixed", input_format: "text", description: "", original_value: "posted" },
        { name: "regions", type: "text_list", role: "changeable", input_format: "[..]", description: "", original_value: ["NA"], allowed: ["NA", "EMEA"] },
      ],
    });
    expect(await screen.findByText("Monthly reporting")).toBeInTheDocument();
    expect(screen.getByText("Comparing regions")).toBeInTheDocument();
    expect(screen.getByText("Daily numbers")).toBeInTheDocument();
    const vars = screen.getByRole("region", { name: "Variables" });
    expect(within(vars).getByRole("columnheader", { name: "Original value" })).toBeInTheDocument();
    expect(within(vars).queryByRole("columnheader", { name: "Default" })).not.toBeInTheDocument();
    expect(within(vars).getByText("2026-01-01 → 2026-04-01")).toBeInTheDocument();
    expect(within(vars).getByText("fixed")).toBeInTheDocument();
    expect(within(vars).getByText("list of text")).toBeInTheDocument();
    expect(within(vars).getByText("NA, EMEA")).toBeInTheDocument();
    expect(screen.getByText(/Trusted SQL · 1 line/)).toBeInTheDocument();
    expect(screen.getByText("No join-map edges touch its tables.")).toBeInTheDocument();
    expect(screen.getByText("No composed definitions use it.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Ann Lee (ann)")).toBeInTheDocument());
  });
});
