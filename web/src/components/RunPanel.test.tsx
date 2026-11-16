import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DefinitionCard, RunOutcome } from "../api/types";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { RunPanel } from "./RunPanel";

const def: DefinitionCard = {
  id: "closed_deals",
  version: 2,
  title: "Closed deals",
  meaning: "One row per closed deal",
  keywords: [],
  use_when: "",
  dont_use_when: "",
  source_dialect: "duckdb",
  profile: "local",
  state: "verified",
  join_keys: [],
  outputs: ["deal_id"],
  tables: ["deals"],
  template: "SELECT 1",
  history: [],
  variables: [
    { name: "period", type: "date_range", role: "changeable", input_format: "{start,end}", description: "Close date window", original_value: { start: "2026-01-01", end: "2026-04-01" } },
    { name: "as_of", type: "date", role: "changeable", input_format: '"YYYY-MM-DD"', description: "", original_value: "2026-04-01" },
    { name: "regions", type: "text_list", role: "changeable", input_format: "[..]", description: "", original_value: ["EMEA"], allowed: ["EMEA", "APAC"] },
    { name: "stage", type: "text", role: "changeable", input_format: '"text"', description: "", original_value: "won" },
    { name: "min_amount", type: "number", role: "fixed", input_format: "number", description: "Deals at least this big", unit: "USD", original_value: 500 },
    { name: "include_test", type: "boolean", role: "changeable", input_format: "true/false", description: "", original_value: false },
    { name: "bucket", type: "grain", role: "changeable", input_format: "day|week|...", description: "", original_value: "month" },
  ],
};

const outcome: RunOutcome = {
  sql: "/* cteforge: closed_deals v2 compile_id=ab12 */\nSELECT 1",
  dialect: "duckdb",
  label: "verified",
  reasons: ["one verified definition"],
  usable: true,
  issues: [{ code: "fixed_override", severity: "warning", message: "min_amount was overridden", variable: "min_amount" }],
  columns: ["deal_id", "amount"],
  rows: [[1, 700], [2, null]],
  row_count: 2,
  truncated: true,
  ran: true,
  compile_id: "ab12",
  definitions: ["closed_deals@2"],
  used: [{ id: "closed_deals", version: 2, state: "verified", verified_by: "ana", verified_at: "2026-11-10" }],
  duration_ms: 12,
};

afterEach(() => vi.unstubAllGlobals());

function setup() {
  const api = mockApi({
    "GET /profiles": () => ({ body: [{ name: "local", dialect: "duckdb" }] }),
    "POST /run": () => ({ body: outcome }),
  });
  render(
    <ToastProvider>
      <RunPanel def={def} />
    </ToastProvider>,
  );
  return api;
}

describe("RunPanel", () => {
  it("generates one control per variable type", async () => {
    setup();
    expect(screen.getByLabelText("Start")).toHaveValue("2026-01-01");
    expect(screen.getByLabelText("End")).toHaveValue("2026-04-01");
    expect(screen.getByLabelText(/as_of/)).toHaveAttribute("type", "date");
    expect(screen.getByText("EMEA")).toBeInTheDocument();
    expect(screen.getByLabelText(/stage/)).toHaveValue("won");
    expect(screen.getByLabelText(/include_test/)).not.toBeChecked();
    expect(screen.getByLabelText(/bucket/)).toHaveValue("month");
    // Fixed variable is read-only until override.
    expect(screen.getByLabelText(/min_amount/)).toBeDisabled();
    expect(screen.getByText("USD")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("option", { name: "local (duckdb)" })).toBeInTheDocument());
  });

  it("posts values in the strict input formats and shows the result", async () => {
    const user = userEvent.setup();
    const api = setup();
    await user.type(screen.getByLabelText(/regions/), "APAC{Enter}");
    await user.click(screen.getByLabelText(/include_test/));
    await user.selectOptions(screen.getByLabelText(/bucket/), "quarter");
    await user.click(screen.getByRole("button", { name: "Run" }));

    const run = await waitFor(() => {
      const c = api.calls.find((x) => x.path === "/run");
      expect(c).toBeDefined();
      return c!;
    });
    expect(run.body).toEqual({
      uses: { closed_deals: { id: "closed_deals", version: 2 } },
      values: {
        closed_deals: {
          period: { start: "2026-01-01", end: "2026-04-01" },
          as_of: "2026-04-01",
          regions: ["EMEA", "APAC"],
          stage: "won",
          include_test: true,
          bucket: "quarter",
        },
      },
      run: true,
      limit: 100,
    });

    const result = await screen.findByRole("region", { name: "Run result" });
    expect(within(result).getAllByText("Verified")[0]).toBeInTheDocument();
    expect(within(result).getByText("one verified definition")).toBeInTheDocument();
    expect(within(result).getByText("fixed_override")).toBeInTheDocument();
    expect(within(result).getByText(/truncated/)).toBeInTheDocument();
    expect(within(result).getByText("ab12")).toBeInTheDocument();
    expect(within(result).getByRole("list", { name: "Definitions used" })).toHaveTextContent(
      "closed_deals@2 · verified · verified by ana on 2026-11-10",
    );
    expect(within(result).getByRole("columnheader", { name: "amount" })).toBeInTheDocument();
  });

  it("sends an overridden fixed number as a JSON number and warns; compile only sets run=false", async () => {
    const user = userEvent.setup();
    const api = setup();
    await user.click(screen.getByLabelText("Override"));
    expect(screen.getByText(/Overriding a fixed variable/)).toBeInTheDocument();
    const amount = screen.getByLabelText(/min_amount/);
    expect(amount).toBeEnabled();
    await user.clear(amount);
    await user.type(amount, "750.25");
    await user.click(screen.getByRole("button", { name: "Compile only" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/run")).toBe(true));
    const body = api.calls.find((c) => c.path === "/run")!.body as { run: boolean; values: Record<string, Record<string, unknown>> };
    expect(body.run).toBe(false);
    expect(body.values.closed_deals?.min_amount).toBe(750.25);
  });

  it("blocks submit and highlights invalid values", async () => {
    const user = userEvent.setup();
    const api = setup();
    await user.clear(screen.getByLabelText("End"));
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("End date is required (YYYY-MM-DD).")).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === "/run")).toBe(false);
  });

  it("warns when a value is outside the allowed list", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/regions/), "LATAM{Enter}");
    expect(screen.getByText(/outside the allowed list/)).toBeInTheDocument();
  });
});
