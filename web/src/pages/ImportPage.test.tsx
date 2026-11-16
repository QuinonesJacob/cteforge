import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ParseReport, ProposalOutcome } from "../api/types";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { ToastViewport } from "../components/ToastViewport";
import { ImportPage } from "./ImportPage";

const report: ParseReport = {
  dialect: "duckdb",
  tables: ["deals"],
  outputs: ["deal_id"],
  ctes: [],
  joins: [],
  schema_found: true,
  issues: [],
  literals: [
    { id: "main.L1", scope: "main", value: "2026-01-01", is_string: true, form: "bare", operator: ">=", column_sql: "d.closed_at", column: { table: "deals", column: "closed_at", type: "DATE" }, wrapped: false, context: [], compared_type: "DATE" },
    { id: "main.L2", scope: "main", value: "2026-04-01", is_string: true, form: "bare", operator: "<", column_sql: "d.closed_at", column: { table: "deals", column: "closed_at", type: "DATE" }, wrapped: false, context: [], compared_type: "DATE" },
    { id: "main.L3", scope: "main", value: "lost", is_string: true, form: "bare", operator: "<>", column_sql: "d.stage", column: { table: "deals", column: "stage", type: "VARCHAR" }, wrapped: false, context: ["negated", "or_branch"], compared_type: "VARCHAR" },
  ],
  ranges: [{ scope: "main", column_sql: "d.closed_at", start: "main.L1", end: "main.L2", shape: "half_open" }],
};

afterEach(() => vi.unstubAllGlobals());

describe("ImportPage", () => {
  it("parse → build variables → describe → submit", async () => {
    const user = userEvent.setup();
    const outcome: ProposalOutcome = {
      proposal: { id: "p1", kind: "new_definition", target_id: "closed_deals", payload: {}, checks: [], summary: "", author: "jq", author_kind: "human", created_at: "2026-11-16T00:00:00Z", status: "approved", auto_applied: true, comment: "" },
      definition: null,
      issues: [{ code: "checks_incomplete", severity: "warning", message: "No warehouse results compared" }],
      auto_applied: true,
    };
    const api = mockApi({
      "GET /me": () => ({ body: { name: "jq", role: "contributor", kind: "human" } }),
      "GET /profiles": () => ({ body: [] }),
      "POST /parse": () => ({ body: report }),
      "POST /proposals/definitions": () => ({ body: outcome }),
      "GET /proposals": () => ({ body: [] }),
    });
    render(
      <MemoryRouter>
        <ToastProvider>
          <AuthProvider>
            <ImportPage />
          </AuthProvider>
        </ToastProvider>
      </MemoryRouter>,
    );

    const sql = await screen.findByLabelText(/Trusted SQL/);
    await user.type(sql, "SELECT * FROM deals");
    await user.click(screen.getByRole("button", { name: "Parse" }));

    // Step 2: literal table with context facts and the suggested pair.
    const table = await screen.findByRole("table");
    expect(within(table).getByText("negated")).toBeInTheDocument();
    expect(within(table).getByText("or_branch")).toBeInTheDocument();
    await user.click(within(table).getAllByRole("button", { name: "select pair" })[0]!);
    await user.click(screen.getByRole("button", { name: "Make variable" }));
    expect(screen.getByLabelText(/^Type/)).toHaveValue("date_range");
    expect(screen.getByLabelText(/^Name/)).toHaveValue("closed_period");
    await user.click(screen.getByLabelText(/Changeable/));
    await user.click(screen.getByRole("button", { name: "Add variable" }));

    // Negated + OR literal made changeable needs a description.
    await user.click(screen.getByLabelText("Select literal main.L3 (lost)"));
    await user.click(screen.getByRole("button", { name: "Make variable" }));
    expect(screen.getByLabelText(/^Type/)).toHaveValue("text");
    await user.click(screen.getByLabelText(/Changeable/));
    expect(screen.getByText(/Required because/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add variable" })).toBeDisabled();
    await user.type(screen.getByLabelText(/Description/), "Stage excluded from the result");
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // Step 3: metadata.
    await user.type(screen.getByLabelText(/^Title/), "Closed deals");
    expect(screen.getByLabelText(/^ID/)).toHaveValue("closed_deals");
    await user.type(screen.getByLabelText(/^Meaning/), "One row per closed deal");
    await user.type(screen.getByLabelText(/^Keywords/), "bookings, won");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // Step 4: submit.
    await user.click(screen.getByRole("button", { name: "Submit proposal" }));
    expect(await screen.findByText("Imported")).toBeInTheDocument();
    expect(screen.getByText("checks_incomplete")).toBeInTheDocument();

    const post = api.calls.find((c) => c.path === "/proposals/definitions")!;
    expect(post.body).toMatchObject({
      sql: "SELECT * FROM deals",
      dialect: "duckdb",
      metadata: { id: "closed_deals", title: "Closed deals", keywords: ["bookings", "won"], owner: "jq" },
      annotations: [
        { name: "closed_period", type: "date_range", role: "changeable", literals: ["main.L1", "main.L2"] },
        { name: "stage", type: "text", role: "changeable", literals: ["main.L3"], description: "Stage excluded from the result" },
      ],
    });
    await waitFor(() => expect(api.calls.filter((c) => c.path === "/parse")).toHaveLength(1));
  });

  function renderPage() {
    render(
      <MemoryRouter>
        <ToastProvider>
          <AuthProvider>
            <ImportPage />
          </AuthProvider>
          <ToastViewport />
        </ToastProvider>
      </MemoryRouter>,
    );
  }

  async function fillMetaAndSubmit(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.type(screen.getByLabelText(/^Title/), "Deals by month");
    await user.type(screen.getByLabelText(/^Meaning/), "One row per month");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Submit proposal" }));
  }

  it("builds a grain variable from grain candidate IDs (M1.5)", async () => {
    const user = userEvent.setup();
    const grainReport: ParseReport = {
      ...report,
      literals: [],
      ranges: [],
      grains: [
        { id: "main.G1", scope: "main", unit: "month", column_sql: "closed_at", sql: "DATE_TRUNC('month', closed_at)", output: "m", context: [] },
        { id: "main.G2", scope: "main", unit: "month", column_sql: "closed_at", sql: "DATE_TRUNC('month', closed_at)", context: [] },
        { id: "main.G3", scope: "main", unit: "hour", column_sql: "closed_at", sql: "DATE_TRUNC('hour', closed_at)", context: [] },
      ],
    };
    const api = mockApi({
      "GET /me": () => ({ body: { name: "jq", role: "contributor", kind: "human" } }),
      "GET /profiles": () => ({ body: [] }),
      "POST /parse": () => ({ body: grainReport }),
      "POST /proposals/definitions": () => ({ body: { proposal: { id: "p1", kind: "new_definition", target_id: "deals_by_month", payload: {}, checks: [], summary: "", author: "jq", author_kind: "human", created_at: "", status: "pending", auto_applied: false, comment: "" }, definition: null, issues: [], auto_applied: false } }),
      "GET /proposals": () => ({ body: [] }),
    });
    renderPage();
    await user.type(await screen.findByLabelText(/Trusted SQL/), "SELECT 1");
    await user.click(screen.getByRole("button", { name: "Parse" }));

    const grains = await screen.findByRole("region", { name: "Grain candidates" });
    expect(screen.getByLabelText("Select grain main.G3 (hour)")).toBeDisabled();
    await user.click(within(grains).getAllByRole("button", { name: "select all" })[0]!);
    await user.click(screen.getByRole("button", { name: "Make variable" }));
    expect(screen.getByLabelText(/^Type/)).toHaveValue("grain");
    expect(screen.getByLabelText(/^Name/)).toHaveValue("grain");
    await user.click(screen.getByLabelText(/Changeable/));
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await fillMetaAndSubmit(user);
    await waitFor(() => expect(api.calls.some((c) => c.path === "/proposals/definitions")).toBe(true));

    const post = api.calls.find((c) => c.path === "/proposals/definitions")!;
    expect(post.body).toMatchObject({
      annotations: [{ name: "grain", type: "grain", role: "changeable", literals: ["main.G1", "main.G2"] }],
    });
  });

  it("won't reuse a parse report after the SQL changes, and drops drafts whose literal moved", async () => {
    const user = userEvent.setup();
    const before: ParseReport = {
      ...report,
      ranges: [],
      literals: [{ ...report.literals[2]!, id: "main.L1", value: "EMEA", operator: "=", column_sql: "d.region", context: [] }],
    };
    const after: ParseReport = {
      ...before,
      literals: [
        { ...before.literals[0]!, id: "main.L1", value: "open", column_sql: "d.status" },
        { ...before.literals[0]!, id: "main.L2" },
      ],
    };
    let parses = 0;
    const api = mockApi({
      "GET /me": () => ({ body: { name: "jq", role: "contributor", kind: "human" } }),
      "GET /profiles": () => ({ body: [] }),
      "POST /parse": () => ({ body: parses++ === 0 ? before : after }),
      "GET /proposals": () => ({ body: [] }),
    });
    renderPage();
    const sql = await screen.findByLabelText(/Trusted SQL/);
    await user.type(sql, "SELECT * FROM deals WHERE region = 'EMEA'");
    await user.click(screen.getByRole("button", { name: "Parse" }));
    await user.click(await screen.findByLabelText("Select literal main.L1 (EMEA)"));
    await user.click(screen.getByRole("button", { name: "Make variable" }));
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    expect(screen.getByText("Variables (1)")).toBeInTheDocument();

    // Edit the SQL: the old report no longer applies.
    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.type(screen.getByLabelText(/Trusted SQL/), " AND 1=1");
    expect(screen.getByText(/changed since the last parse/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to variables" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Variables/ })).not.toBeInTheDocument(); // stepper

    // Re-parse: main.L1 now names 'open', so the `region` draft is removed, not rebound.
    await user.click(screen.getByRole("button", { name: "Parse again" }));
    expect(await screen.findByText("Variables (0)")).toBeInTheDocument();
    expect(screen.getByText(/Removed 1 variable after re-parsing/)).toBeInTheDocument();
    expect(api.calls.filter((c) => c.path === "/parse")).toHaveLength(2);
  });

  it("tells viewers they cannot propose", async () => {
    mockApi({ "GET /me": () => ({ body: { name: "v", role: "viewer", kind: "human" } }) });
    render(
      <MemoryRouter>
        <ToastProvider>
          <AuthProvider>
            <ImportPage />
          </AuthProvider>
        </ToastProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/can't propose definitions/)).toBeInTheDocument();
  });
});
