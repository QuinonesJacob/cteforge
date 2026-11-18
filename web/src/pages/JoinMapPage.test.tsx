import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JoinCard } from "../api/types";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { JoinMapPage } from "./JoinMapPage";

afterEach(() => vi.unstubAllGlobals());

const edge: JoinCard = {
  id: "deals__invoices__deal_id=deal_id",
  left_table: "deals",
  right_table: "invoices",
  keys: [{ left: "deal_id", right: "deal_id" }],
  join_type: "inner",
  relationship: "one_to_many",
  usage_count: 2,
  traps: ["fan-out: a deal has many invoices"],
  notes: "",
  sources: ["realization_rate"],
  evidence: {
    claimed: "one_to_many",
    observed: "one_to_many",
    left: { table: "deals", keys: ["deal_id"], rows: 10, distinct_keys: 10 },
    right: { table: "invoices", keys: ["deal_id"], rows: 14, distinct_keys: 9 },
    consistent: true,
  },
  known: true,
  confirmed: false,
  pending_proposals: ["p7"],
};
const added: JoinCard = {
  ...edge,
  id: "accounts__regions__region=region",
  left_table: "accounts",
  right_table: "regions",
  keys: [{ left: "region", right: "region" }],
  relationship: null,
  traps: [],
  sources: [],
  evidence: null,
  added_by: "bot",
  added_by_kind: "agent",
  known: false,
  pending_proposals: [],
};

const outcome = {
  proposal: { id: "p8", kind: "new_join", target_id: edge.id, payload: {}, checks: [], summary: "", author: "ann", author_kind: "human", created_at: "", status: "pending", auto_applied: false, comment: "" },
  issues: [],
  auto_applied: false,
};

function renderPage() {
  render(
    <MemoryRouter>
      <ToastProvider>
        <AuthProvider>
          <JoinMapPage />
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("JoinMapPage (ADR 0026)", () => {
  it("lists edges with sources, known/confirmed, evidence and pending proposals", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human" } }),
      "GET /joins": () => ({ body: [edge, added] }),
      "GET /proposals": () => ({
        body: [
          {
            ...outcome.proposal,
            id: "p9",
            target_id: "deals__invoices__status=status",
            author: "bot",
            author_kind: "agent",
            payload: { action: "add", join: { ...added, id: "deals__invoices__status=status", left_table: "deals", right_table: "invoices", keys: [{ left: "status", right: "status" }] } },
          },
        ],
      }),
    });
    renderPage();
    const proposed = await screen.findByRole("region", { name: "Proposed joins" });
    expect(within(proposed).getByRole("link", { name: "deals ↔ invoices" })).toHaveAttribute("href", "/review/p9");
    expect(within(proposed).getByText(/by bot \(agent\)/)).toBeInTheDocument();
    const row = (await screen.findByText("fan-out: a deal has many invoices")).closest("tr")!;
    expect(within(row).getByRole("link", { name: "realization_rate" })).toHaveAttribute("href", "/definitions/realization_rate");
    expect(within(row).getByText("known")).toBeInTheDocument();
    expect(within(row).getAllByText("one to many").length).toBeGreaterThan(0);
    expect(within(row).getByRole("link", { name: "pending change" })).toHaveAttribute("href", "/review/p7");
    expect(within(row).getByText(/invoices: 14 rows, 9 distinct keys \(many\)/)).toBeInTheDocument();
    const other = screen.getByText("accounts", { exact: false, selector: "td" }).closest("tr")!;
    expect(within(other).getByText("not known")).toBeInTheDocument();
    expect(within(other).getByText(/added by bot \(agent\)/)).toBeInTheDocument();
    // A viewer can't propose: no forms.
    expect(screen.queryByRole("button", { name: "Add a join" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Annotate/ })).not.toBeInTheDocument();
  });

  it("proposes an annotation and a new edge", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "contributor", kind: "human" } }),
      "GET /joins": () => ({ body: [edge] }),
      "POST /proposals/joins": () => ({ body: outcome }),
      "GET /proposals/count": () => ({ body: { count: 1 } }),
      "GET /proposals": () => ({ body: [] }),
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: `Annotate ${edge.id}` }));
    const form = screen.getByRole("form", { name: `Annotate ${edge.id}` });
    expect(within(form).getByRole("button", { name: "Propose annotation" })).toBeDisabled();
    await user.type(within(form).getByLabelText("Notes"), "Invoices can be split.");
    await user.click(within(form).getByRole("button", { name: "Propose annotation" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")?.body).toEqual({
      action: "annotate",
      id: edge.id,
      notes: "Invoices can be split.",
      summary: "",
    });
    expect(await screen.findByText(/Proposed — waiting for review/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add a join" }));
    const add = screen.getByRole("form", { name: "Add a join" });
    await user.click(within(add).getByRole("button", { name: "Propose join" }));
    expect(within(add).getByRole("alert")).toHaveTextContent("Name both tables.");
    await user.type(within(add).getByLabelText("Left table"), "accounts");
    await user.type(within(add).getByLabelText("Right table"), "regions");
    await user.type(within(add).getByLabelText("Left column 1"), "region");
    await user.type(within(add).getByLabelText("Right column 1"), "region");
    await user.click(within(add).getByRole("button", { name: "Propose join" }));
    await waitFor(() => expect(api.calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(api.calls.filter((c) => c.method === "POST")[1]?.body).toEqual({
      action: "add",
      left_table: "accounts",
      right_table: "regions",
      keys: [{ left: "region", right: "region" }],
      join_type: "inner",
      summary: "",
    });
  });
});
