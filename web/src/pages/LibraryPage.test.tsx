import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { LibraryPage } from "./LibraryPage";

afterEach(() => vi.unstubAllGlobals());

const hit = (id: string, match: string, note: string | null = null) => ({
  id,
  version: 1,
  title: id,
  meaning: "",
  state: "llm_annotated",
  score: 0.03,
  match,
  note,
  keywords: [],
  variables: [],
});

function renderPage(path = "/?q=money") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <AuthProvider>
          <LibraryPage />
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("LibraryPage search", () => {
  it("marks meaning-only hits", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human" } }),
      "GET /definitions": () => ({ body: [hit("by_words", "both"), hit("by_meaning", "meaning")] }),
    });
    renderPage();
    expect(await screen.findByText("by_meaning")).toBeInTheDocument();
    expect(screen.getAllByText("matched by meaning")).toHaveLength(1);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("shows the note when meaning search could not run", async () => {
    const note = "Meaning search is unavailable; these results match by keyword only.";
    mockApi({
      "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human" } }),
      "GET /definitions": () => ({ body: [hit("a", "keyword", note), hit("b", "keyword", note)] }),
    });
    renderPage();
    expect(await screen.findByRole("note")).toHaveTextContent(note);
    expect(screen.queryByText("matched by meaning")).not.toBeInTheDocument();
  });
});
