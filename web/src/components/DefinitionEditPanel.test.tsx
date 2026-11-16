import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DefinitionCard, ProposalOutcome } from "../api/types";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { DefinitionEditPanel } from "./DefinitionEditPanel";

const def: DefinitionCard = {
  id: "rr", version: 2, title: "Realization rate", meaning: "Collected / booked", keywords: ["rr"], use_when: "",
  dont_use_when: "", source_dialect: "duckdb", state: "verified", owner: "ana", join_keys: [], outputs: [], tables: [],
  variables: [], template: "",
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
    });
    const onApplied = vi.fn();
    render(
      <MemoryRouter>
        <ToastProvider>
          <DefinitionEditPanel def={def} onApplied={onApplied} />
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
});
