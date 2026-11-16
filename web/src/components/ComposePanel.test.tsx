import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DefinitionCard, RunOutcome } from "../api/types";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { ComposePanel } from "./ComposePanel";

const card = (id: string): DefinitionCard => ({
  id, version: 1, title: id.toUpperCase(), meaning: "", keywords: [], use_when: "", dont_use_when: "",
  source_dialect: "duckdb", state: "verified", join_keys: ["region"], outputs: ["region"], tables: [], template: "",
  variables: [{ name: "regions", type: "text_list", role: "changeable", input_format: "[..]", description: "", original_value: ["EMEA"] }],
});

const outcome: RunOutcome = {
  sql: "WITH rr AS (SELECT 1) SELECT * FROM rr", dialect: "duckdb", label: "built_from_verified_parts", reasons: [],
  usable: true, issues: [], columns: ["region"], rows: [["APAC"]], row_count: 1, truncated: false, ran: true,
  compile_id: "c1", definitions: ["rr@1"], used: [], duration_ms: 3,
};

afterEach(() => vi.unstubAllGlobals());

function setup(role = "contributor") {
  const api = mockApi({
    "GET /me": () => ({ body: { name: "jq", role, kind: "human" } }),
    "GET /profiles": () => ({ body: [] }),
    "GET /definitions": () => ({ body: [{ id: "booked", version: 1, title: "Booked", meaning: "", state: "verified", score: 1, keywords: [], variables: [] }] }),
    "GET /definitions/realization_rate": () => ({ body: card("realization_rate") }),
    "GET /definitions/booked": () => ({ body: card("booked") }),
    "POST /run": () => ({ body: outcome }),
    "POST /proposals/recipes": (body) => ({
      body: {
        proposal: { id: "p9", kind: "new_recipe", target_id: (body as { recipe: { id: string } }).recipe.id, payload: {}, checks: [], summary: "", author: "jq", author_kind: "human", created_at: "2026-11-16T00:00:00Z", status: "approved", auto_applied: true, comment: "" },
        definition: null, issues: [], auto_applied: true,
      },
    }),
    "GET /proposals/count": () => ({ body: { count: 0 } }),
  });
  render(
    <MemoryRouter>
      <ToastProvider>
        <AuthProvider>
          <ComposePanel
            seed={{ parts: [{ alias: "rr", ref: { id: "realization_rate" }, saved: { regions: ["APAC"] } }], select: "", recipeId: "apac" , title: "APAC" }}
          />
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
  return api;
}

describe("ComposePanel", () => {
  it("combines definitions by alias with a final SELECT, runs, and saves a recipe", async () => {
    const user = userEvent.setup();
    const api = setup();
    await waitFor(() => expect(screen.getByRole("group", { name: "Alias rr" })).toHaveTextContent("REALIZATION_RATE"));
    await user.selectOptions(await screen.findByLabelText("Add a definition"), "booked");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.getByRole("group", { name: "Alias booked" })).toHaveTextContent("BOOKED"));
    await user.type(screen.getByLabelText(/Final SELECT/), "SELECT * FROM rr JOIN booked USING (region)");
    await user.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(api.calls.some((c) => c.path === "/run")).toBe(true));
    const run = api.calls.find((c) => c.path === "/run")?.body as Record<string, unknown>;
    expect(run.uses).toEqual({ rr: { id: "realization_rate" }, booked: { id: "booked" } });
    expect(run.values).toEqual({ rr: { regions: ["APAC"] }, booked: { regions: ["EMEA"] } });
    expect(run.select).toBe("SELECT * FROM rr JOIN booked USING (region)");

    expect(await screen.findByRole("form", { name: "Save as recipe" })).toBeInTheDocument();
    expect((screen.getByLabelText(/Recipe ID/) as HTMLInputElement).value).toBe("apac");
    await user.click(screen.getByRole("button", { name: "Propose recipe" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/proposals/recipes")).toBe(true));
    const saved = api.calls.find((c) => c.path === "/proposals/recipes")?.body as { recipe: Record<string, unknown> };
    expect(saved.recipe).toMatchObject({ id: "apac", title: "APAC", dialect: "duckdb", uses: run.uses, values: run.values, select: run.select });
    expect(await screen.findByText("Recipe saved")).toBeInTheDocument();
  });

  it("does not offer saving to a viewer", async () => {
    const user = userEvent.setup();
    setup("viewer");
    await waitFor(() => expect(screen.getByRole("group", { name: "Alias rr" })).toHaveTextContent("REALIZATION_RATE"));
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByLabelText("Run result")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Save as recipe" })).toBeNull();
  });
});
