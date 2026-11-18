import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LibraryRow, ProposalBrief, ProposalKind } from "../api/types";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { LibraryPage } from "./LibraryPage";

afterEach(() => vi.unstubAllGlobals());

const brief = (id: string, kind: ProposalKind, author = "bot"): ProposalBrief => ({
  id,
  kind,
  status: "pending",
  author,
  author_kind: "agent",
  created_at: "2026-11-17T00:00:00Z",
  summary: "",
});

const row = (partial: Partial<LibraryRow> & Pick<LibraryRow, "id">): LibraryRow => ({
  title: partial.id,
  meaning: "",
  version: 1,
  state: "llm_annotated",
  composed: false,
  approval: "approved",
  pending: [],
  needs_my_review: false,
  ...partial,
});

const page = (rows: LibraryRow[]) => ({ body: { rows, total: rows.length, pending_count: 2, proposals_visible: true } });

function renderPage(path = "/") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<LibraryPage />} />
          </Routes>
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("LibraryPage (ADR 0026, 0029)", () => {
  it("shows definitions, a pending new definition and revision badges", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "approver", kind: "human" } }),
      "GET /library": () =>
        page([
          row({ id: "new_one", version: null, state: "llm_annotated", approval: "pending", latest_proposal: brief("p1", "new_definition"), pending: [brief("p1", "new_definition")], needs_my_review: true }),
          row({ id: "rr", pending: [brief("p2", "revise_definition"), brief("p3", "rename_definition")] }),
          row({ id: "combo", composed: true, created_by: "bot", created_by_kind: "agent", state: "verified" }),
        ]),
    });
    renderPage();
    expect(await screen.findByRole("link", { name: "new_one" })).toHaveAttribute("href", "/review/p1");
    expect(screen.getByText("Pending approval")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Verified" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Revision pending" })).toHaveAttribute("href", "/review/p2");
    expect(screen.getByRole("link", { name: "Rename pending" })).toHaveAttribute("href", "/review/p3");
    expect(screen.getByRole("link", { name: "rr" })).toHaveAttribute("href", "/definitions/rr");
    expect(screen.getByText("composed")).toBeInTheDocument();
    expect(screen.getByText(/Created by bot \(agent\)/)).toBeInTheDocument();
    // Default view: rejected and withdrawn are not checked; approval is left to the server.
    await userEvent.click(screen.getByRole("button", { name: /Filters/ }));
    expect(screen.getByRole("checkbox", { name: "Rejected" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Pending" })).toBeChecked();
  });

  it("sends the filters to the server", async () => {
    const user = userEvent.setup();
    const api = mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "approver", kind: "human" } }),
      "GET /library": () => page([row({ id: "rr" })]),
    });
    renderPage();
    await screen.findByRole("link", { name: "rr" });
    const libraryUrls = () => api.fn.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/library"));
    expect(libraryUrls()[0]).toBe("/api/v1/library");
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    await user.click(screen.getByRole("checkbox", { name: "Verified" }));
    await user.click(screen.getByRole("checkbox", { name: "Rejected" }));
    await waitFor(() =>
      expect(libraryUrls().at(-1)).toBe("/api/v1/library?trust=verified&approval=pending&approval=approved&approval=rejected"),
    );
    await user.click(screen.getByRole("checkbox", { name: "Needs my review" }));
    await waitFor(() => expect(libraryUrls().at(-1)).toContain("needs_my_review=true"));
    // Active filters are removable chips; Clear all returns to the default view.
    await user.click(screen.getByRole("button", { name: "Remove filter Trust: Verified" }));
    await waitFor(() => expect(libraryUrls().at(-1)).not.toContain("trust="));
    await user.click(screen.getAllByRole("button", { name: "Clear all" })[0]!);
    await waitFor(() => expect(libraryUrls().at(-1)).toBe("/api/v1/library"));
  });

  it("shows proposals waiting on you, oldest first, with +/− lines; its link opens the review queue oldest first", async () => {
    const user = userEvent.setup();
    const waiting = [
      { ...brief("p1", "revise_definition", "mlee"), target_id: "gross", title: "Gross bookings", created_at: "2026-11-10T00:00:00Z", lines_added: 4, lines_removed: 1, needs_my_review: true },
      { ...brief("p2", "new_definition", "apatel"), target_id: "active", title: "Active customers", lines_added: 38, lines_removed: 0, needs_my_review: true },
      { ...brief("p3", "revise_definition", "jdoe"), target_id: "refunds", title: "Refunds", lines_added: 0, lines_removed: 0, needs_my_review: true },
    ];
    const api = mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "approver", kind: "human" } }),
      "GET /library": () => ({ body: { rows: [row({ id: "rr" })], total: 1, pending_count: 2, proposals_visible: true, waiting } }),
    });
    renderPage();
    const panel = await screen.findByRole("region", { name: "3 proposals" });
    const items = within(panel).getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("/review/"));
    expect(items.map((a) => a.getAttribute("href"))).toEqual(["/review/p1", "/review/p2", "/review/p3"]);
    // ADR 0029: counts are of the query; no query change says so.
    expect(within(items[2]!).getByText("metadata only")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Gross bookings")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Change")).toBeInTheDocument();
    expect(within(items[0]!).getByText("+4")).toBeInTheDocument();
    expect(within(items[0]!).getByText("−1")).toBeInTheDocument();
    expect(within(items[0]!).getByText("by mlee (agent)")).toBeInTheDocument();
    const queue = within(panel).getByRole("link", { name: "Open review queue →" });
    expect(queue).toHaveAttribute("href", "/?review=mine&sort=oldest");
    await user.click(queue);
    await waitFor(() =>
      expect(api.fn.mock.calls.map((c) => String(c[0])).at(-1)).toBe("/api/v1/library?needs_my_review=true&sort=oldest"),
    );
    expect(await screen.findByText(/oldest first/)).toBeInTheDocument();
  });

  it("opens with Needs my review from /?review=mine and hides it without review_proposal", async () => {
    const api = mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "approver", kind: "human" } }),
      "GET /library": () => page([]),
    });
    renderPage("/?review=mine");
    expect(await screen.findByText("Nothing needs your review")).toBeInTheDocument();
    const urls = api.fn.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/library"));
    expect(urls.at(-1)).toContain("needs_my_review=true");
  });

  it("offers no review or approval filters to a narrow token", async () => {
    const api = mockApi({
      "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human", tools: ["search", "get_definition"] } }),
      "GET /library": () => ({ body: { rows: [row({ id: "rr" })], total: 1, pending_count: 0, proposals_visible: false } }),
    });
    renderPage("/?review=mine");
    await screen.findByRole("link", { name: "rr" });
    await userEvent.click(screen.getByRole("button", { name: /Filters/ }));
    expect(screen.queryByRole("checkbox", { name: "Needs my review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /Waiting|proposals/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Pending" })).not.toBeInTheDocument();
    const urls = api.fn.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/library"));
    expect(urls.every((u) => !u.includes("needs_my_review"))).toBe(true);
  });
});
