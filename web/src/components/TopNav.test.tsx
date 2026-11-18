import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../state/AuthContext";
import { ThemeProvider } from "../state/ThemeContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { TopNav } from "./TopNav";

afterEach(() => vi.unstubAllGlobals());

function renderNav() {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <TopNav />
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("TopNav (ADR 0026)", () => {
  it("is Library · Join map · Import · Audit · Settings, with the pending badge on Library", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "admin", kind: "human" } }),
      "GET /proposals/count": () => ({ body: { count: 3 } }),
    });
    renderNav();
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    await within(nav).findByText("Settings");
    expect(within(nav).getAllByRole("link").map((a) => a.textContent?.replace(/\d+$/, ""))).toEqual([
      "Library",
      "Join map",
      "Import",
      "Audit",
      "Settings",
    ]);
    expect(await within(nav).findByLabelText("3 pending")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /Library/ })).toContainElement(within(nav).getByLabelText("3 pending"));
    expect(within(nav).queryByText("Explore")).not.toBeInTheDocument();
    expect(within(nav).queryByText("Review")).not.toBeInTheDocument();
  });

  it("hides what the token can't use", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human", tools: ["search", "get_definition"] } }),
    });
    renderNav();
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    await within(nav).findByText("Settings");
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Library", "Settings"]);
  });
});
