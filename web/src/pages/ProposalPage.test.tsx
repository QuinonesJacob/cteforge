import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProposalView } from "../api/types";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { ProposalPage } from "./ProposalPage";

afterEach(() => vi.unstubAllGlobals());

const base: ProposalView = {
  id: "p1",
  kind: "revise_definition",
  target_id: "gross_bookings",
  title: "Gross bookings by period",
  base_version: 6,
  payload: {},
  checks: [],
  summary: "Internal test orgs were inflating EMEA bookings.",
  author: "mlee",
  author_kind: "human",
  created_at: "2026-11-17T00:00:00Z",
  status: "pending",
  auto_applied: false,
  comment: "",
  diff: "--- gross_bookings@v6\n+++ gross_bookings (proposed)\n@@ -1,2 +1,2 @@\n-version: 6\n+version: 7\n state: verified\n",
  impact: [],
};

function renderPage(view: ProposalView) {
  const api = mockApi({
    "GET /me": () => ({ body: { name: "ann", role: "approver", kind: "human" } }),
    "GET /proposals/p1": () => ({ body: view }),
    "GET /definitions/gross_bookings": () => ({ body: { owner: null, state: "verified" } }),
  });
  render(
    <MemoryRouter initialEntries={["/review/p1"]}>
      <ToastProvider>
        <AuthProvider>
          <Routes>
            <Route path="/review/:id" element={<ProposalPage />} />
          </Routes>
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
  return api;
}

describe("ProposalPage (ADR 0029)", () => {
  it("shows the query change, not the stored YAML, and the described fields separately", async () => {
    const api = renderPage({
      ...base,
      sql_diff:
        "--- gross_bookings@v6.sql\n+++ gross_bookings (proposed).sql\n@@ -1,3 +1,4 @@\n SELECT region\n FROM c\n-WHERE status = 'signed'\n+WHERE status = 'signed'\n+  AND NOT a.is_test_org\n",
      metadata_changes: [
        { field: "dont_use_when", before: "Pipeline", after: "Pipeline; test accounts" },
        { field: "keywords", before: ["bookings"], after: ["bookings", "test orgs"] },
      ],
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Gross bookings by period" })).toBeInTheDocument();
    expect(api.fn.mock.calls.map((c) => String(c[0]))).toContain("/api/v1/proposals/p1?full_diff=true");
    const diff = screen.getByRole("region", { name: "Unified diff" });
    expect(within(diff).getByText("AND NOT a.is_test_org")).toBeInTheDocument();
    expect(within(diff).queryByText(/version: 7/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("2 lines added")).toHaveTextContent("+2");
    expect(screen.getByLabelText("1 lines removed")).toHaveTextContent("−1");
    const meta = screen.getByRole("region", { name: "Description changes" });
    expect(within(meta).getByText("Don't use when")).toBeInTheDocument();
    expect(within(meta).getByText("Pipeline; test accounts")).toBeInTheDocument();
    expect(within(meta).getByText("test orgs")).toBeInTheDocument();
    // The toggles still work on the query diff.
    await userEvent.click(screen.getByRole("button", { name: "Side by side" }));
    expect(screen.getByRole("region", { name: "Side-by-side diff" })).toBeInTheDocument();
  });

  it("says the query does not change for a metadata-only proposal", async () => {
    renderPage({ ...base, sql_diff: "", metadata_changes: [{ field: "title", before: "Old", after: "New" }] });
    expect(await screen.findByText(/The query does not change/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Unified diff" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Description changes" })).toHaveTextContent(/Old.*→.*New/);
  });
});
