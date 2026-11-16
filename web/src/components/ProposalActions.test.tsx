import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProposalView, User } from "../api/types";
import { canDecide } from "../lib/roles";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { ProposalActions } from "./ProposalActions";

const proposal: ProposalView = {
  id: "p1",
  kind: "revise_definition",
  target_id: "closed_deals",
  base_version: 1,
  payload: {},
  checks: [],
  summary: "Tighten the filter",
  author: "ana",
  author_kind: "human",
  created_at: "2026-11-16T00:00:00Z",
  status: "pending",
  auto_applied: false,
  comment: "",
};
const jq: User = { name: "jq", role: "contributor", kind: "human" };

afterEach(() => vi.unstubAllGlobals());

function renderActions(props: Partial<Parameters<typeof ProposalActions>[0]>) {
  return render(
    <ToastProvider>
      <ProposalActions proposal={proposal} user={jq} onDone={() => undefined} {...props} />
    </ToastProvider>,
  );
}

describe("canDecide", () => {
  const bot: User = { name: "bot", role: "approver", kind: "agent" };
  it("approvers decide anything; owners decide definition proposals only", () => {
    expect(canDecide({ ...jq, role: "approver" }, "new_join", null)).toBe(true);
    expect(canDecide(jq, "revise_definition", { owner: "jq" })).toBe(true);
    expect(canDecide(jq, "revise_definition", { owner: "ana" })).toBe(false);
    expect(canDecide(jq, "new_recipe", { owner: "jq" })).toBe(false);
    expect(canDecide({ ...jq, role: "viewer" }, "revise_definition", { owner: "jq" })).toBe(false);
    expect(canDecide(null, "revise_definition", { owner: "jq" })).toBe(false);
  });
  it("agents can't decide changes to a verified definition", () => {
    expect(canDecide(bot, "revise_definition", { owner: null, state: "verified" })).toBe(false);
    expect(canDecide(bot, "revise_definition", { owner: null, state: "llm_annotated" })).toBe(true);
  });
});

describe("ProposalActions", () => {
  it("lets a contributor who owns the target approve or reject, without verify", async () => {
    const api = mockApi({
      "POST /proposals/p1/approve": () => ({ body: { proposal: { ...proposal, status: "approved" }, issues: [], auto_applied: false } }),
      "GET /proposals": () => ({ body: [] }),
    });
    const done = vi.fn();
    renderActions({ target: { owner: "jq", state: "llm_annotated" }, onDone: done });
    expect(screen.getByText(/You own this definition/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Also verify/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(api.calls.find((c) => c.path === "/proposals/p1/approve")?.body).toEqual({ verify: false, comment: "" });
    expect(done).toHaveBeenCalled();
  });

  it("offers no decision to a contributor who does not own the target", () => {
    renderActions({ target: { owner: "ana" } });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText(/Only approvers/)).toBeInTheDocument();
  });

  it("says it is checking while the owner is loading", () => {
    renderActions({ target: null, targetLoading: true });
    expect(screen.getByText(/Checking who can decide/)).toBeInTheDocument();
  });
});
