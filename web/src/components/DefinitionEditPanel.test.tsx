import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DefinitionCard, DefinitionReferences, ProposalOutcome } from "../api/types";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { DefinitionEditPanel } from "./DefinitionEditPanel";

const def: DefinitionCard = {
  id: "rr", version: 2, title: "Realization rate", meaning: "Collected / booked", keywords: ["rr"], use_when: "",
  dont_use_when: "", source_dialect: "duckdb", state: "verified", owner: "ana", join_keys: [], outputs: [], tables: [],
  variables: [], template: "",
};

const refs: DefinitionReferences = {
  id: "rr", version: 2, state: "verified", former_ids: [], outside_warning: "Outside references break.",
  recipes: [{ recipe_id: "rr_emea", recipe_version: 1, title: "EMEA", state: "llm_annotated", alias: "rr", pinned_version: null }],
  proposals: [{ proposal_id: "p9", kind: "new_recipe", target_id: "r9", summary: "Saved Q3", author: "bot", created_at: "2026-11-16T00:00:00Z", relation: "uses" }],
};

afterEach(() => vi.unstubAllGlobals());

describe("DefinitionEditPanel", () => {
  it("proposes a metadata-only revision with just the changed keys", async () => {
    const user = userEvent.setup();
    const outcome: ProposalOutcome = {
      proposal: { id: "p3", kind: "revise_definition", target_id: "rr", base_version: 2, payload: {}, checks: [], summary: "", author: "jq", author_kind: "human", created_at: "2026-11-16T00:00:00Z", status: "pending", auto_applied: false, comment: "" },
      definition: null, issues: [], auto_applied: false,
    };
    const api = mockApi({
      "POST /proposals/definitions/rr/revise": () => ({ body: outcome }),
      "GET /proposals/count": () => ({ body: { count: 1 } }),
      "GET /definitions/rr/references": () => ({ body: refs }),
    });
    const onApplied = vi.fn();
    render(
      <MemoryRouter>
        <ToastProvider>
          <DefinitionEditPanel def={def} onApplied={onApplied} onRenamed={vi.fn()} />
        </ToastProvider>
      </MemoryRouter>,
    );
    const propose = screen.getByRole("button", { name: "Propose v3" });
    expect(propose).toBeDisabled();
    expect(screen.getByRole("link", { name: /Change SQL or variables/ })).toHaveAttribute("href", "/import?revise=rr");
    await user.type(screen.getByLabelText(/^Use when/), "cash questions");
    await user.click(propose);
    await waitFor(() => expect(api.calls.some((c) => c.path === "/proposals/definitions/rr/revise")).toBe(true));
    const body = api.calls.find((c) => c.path === "/proposals/definitions/rr/revise")?.body as Record<string, unknown>;
    expect(body).toEqual({ base_version: 2, metadata: { use_when: "cash questions" }, summary: "Edit use_when of rr" });
    expect(await screen.findByText("Proposal submitted")).toBeInTheDocument();
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("renames only after the new ID is typed twice, showing what uses it", async () => {
    const user = userEvent.setup();
    const outcome: ProposalOutcome = {
      proposal: { id: "p4", kind: "rename_definition", target_id: "rr", base_version: 2, payload: { from_id: "rr", to_id: "rr_monthly" }, checks: [], summary: "", author: "jq", author_kind: "human", created_at: "2026-11-16T00:00:00Z", status: "pending", auto_applied: false, comment: "" },
      definition: null, issues: [], auto_applied: false,
    };
    const api = mockApi({
      "GET /definitions/rr/references": () => ({ body: refs }),
      "POST /proposals/definitions/rr/rename": () => ({ body: outcome }),
      "GET /proposals/count": () => ({ body: { count: 1 } }),
    });
    const onRenamed = vi.fn();
    render(
      <MemoryRouter>
        <ToastProvider>
          <DefinitionEditPanel def={def} onApplied={vi.fn()} onRenamed={onRenamed} />
        </ToastProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText("1 recipe")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Saved Q3" })).toHaveAttribute("href", "/review/p9");
    expect(screen.getByText(/Outside references break/)).toBeInTheDocument();
    const rename = screen.getByRole("button", { name: "Rename ID" });
    expect(rename).toBeDisabled();
    await user.type(screen.getByLabelText(/^New ID/), "rr_monthly");
    await user.type(screen.getByLabelText(/^Type the new ID to confirm/), "rr_month");
    expect(screen.getByRole("button", { name: "Rename to rr_monthly" })).toBeDisabled();
    await user.type(screen.getByLabelText(/^Type the new ID to confirm/), "ly");
    await user.click(screen.getByRole("button", { name: "Rename to rr_monthly" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/proposals/definitions/rr/rename")).toBe(true));
    const body = api.calls.find((c) => c.path === "/proposals/definitions/rr/rename")?.body;
    expect(body).toEqual({ new_id: "rr_monthly", base_version: 2, summary: "" });
    expect(await screen.findByText("Rename proposal submitted")).toBeInTheDocument();
    expect(onRenamed).not.toHaveBeenCalled();
  });
});
