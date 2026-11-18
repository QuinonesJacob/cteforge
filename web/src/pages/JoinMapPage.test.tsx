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
  sources: ["realization_rate", "booked"],
  trusted_sources: ["realization_rate"],
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
  source_joins: [
    { definition: "realization_rate", join_type: "left", location: "cte", scope: "monthly" },
    { definition: "booked", join_type: "inner", location: "main", scope: null },
  ],
  source_states: { realization_rate: "verified", booked: "llm_annotated" },
  source_titles: { realization_rate: "Realization rate", booked: "Booked vs collected" },
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
  trusted_sources: [],
  source_joins: [],
  source_states: {},
  source_titles: {},
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

function renderPage(path = "/joins") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <AuthProvider>
          <JoinMapPage />
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("JoinMapPage (ADR 0026, 0029 list view)", () => {
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
    // one_to_many: the many side (invoices) comes first.
    const row = (await screen.findByRole("button", { name: "invoices" })).closest("tr")!;
    expect(within(row).getByText("deals")).toBeInTheDocument();
    expect(within(row).getByText("deal_id = deal_id")).toBeInTheDocument();
    expect(within(row).getByText("LEFT 1")).toBeInTheDocument();
    expect(within(row).getByText("INNER 1")).toBeInTheDocument();
    expect(within(row).getByText("2 variants")).toBeInTheDocument();
    expect(within(row).getByRole("img", { name: "1 verified, 1 LLM-annotated" })).toBeInTheDocument();
    expect(within(row).getByText("Known")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Change pending" })).toHaveAttribute("href", "/review/p7");
    // Unknown relationship: plain left / right.
    const other = (await screen.findByRole("button", { name: "accounts" })).closest("tr")!;
    expect(within(other).getByText("Not known")).toBeInTheDocument();
    expect(within(other).getByText("left")).toBeInTheDocument();
    // A pending addition is a faded "Proposed" row linking to its proposal.
    const proposed = screen.getByRole("link", { name: "Proposed" });
    expect(proposed).toHaveAttribute("href", "/review/p9");
    expect(proposed.closest("tr")).toHaveTextContent(/by bot \(agent\)/);

    // The row's detail: each source with its join type and location, evidence, traps.
    await userEvent.click(within(row).getByRole("button", { name: "invoices" }));
    const detail = document.getElementById(`join-detail-${edge.id}`)!;
    expect(within(detail).getByRole("link", { name: "Realization rate" })).toHaveAttribute("href", "/definitions/realization_rate");
    expect(within(detail).getByText(/in CTE monthly/)).toBeInTheDocument();
    expect(within(detail).getByText(/in main query/)).toBeInTheDocument();
    expect(within(detail).getByText("agent only")).toBeInTheDocument(); // booked is not trusted
    expect(within(detail).getByText(/invoices: 14 rows, 9 distinct keys \(many\)/)).toBeInTheDocument();
    expect(within(detail).getByText("fan-out: a deal has many invoices")).toBeInTheDocument();
    // A viewer can't propose: no forms, and nobody adds joins here (ADR 0029).
    expect(screen.queryByRole("button", { name: "Add a join" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Annotate/ })).not.toBeInTheDocument();
    // No "known only" filter (ADR 0029); "Variants first" stays.
    expect(screen.queryByRole("checkbox", { name: /Known/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Variants first" })).toBeInTheDocument();
  });

  it("searches without a round trip and filters by definition", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human", tools: ["list_joins"] } }),
      "GET /joins": () => ({ body: [edge, added] }),
    });
    renderPage("/joins?definition=realization_rate");
    expect(await screen.findByText("Definition:")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "/joins")).toBeDefined();
    expect(api.fn.mock.calls.map((c) => String(c[0])).find((u) => u.includes("/joins"))).toContain("definition=realization_rate");
    await user.type(screen.getByLabelText("Search joins"), "booked");
    expect(screen.getByText(/1 of 2 joins match/)).toBeInTheDocument();
    expect((await screen.findByRole("button", { name: "accounts" })).closest("tr")).toHaveClass("row-faded");
  });

  it("proposes an annotation from a row's detail", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "contributor", kind: "human" } }),
      "GET /joins": () => ({ body: [edge] }),
      "POST /proposals/joins": () => ({ body: outcome }),
      "GET /proposals/count": () => ({ body: { count: 1 } }),
      "GET /proposals": () => ({ body: [] }),
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "invoices" }));
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
    expect(screen.queryByRole("button", { name: "Add a join" })).not.toBeInTheDocument();
  });
});
