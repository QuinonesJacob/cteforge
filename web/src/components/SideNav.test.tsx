import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../state/AuthContext";
import { ThemeProvider } from "../state/ThemeContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { SideNav } from "./SideNav";

afterEach(() => vi.unstubAllGlobals());

function renderNav() {
  render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <SideNav />
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

const names = (nav: HTMLElement) => within(nav).getAllByRole("link").map((a) => a.textContent?.replace(/\d+$/, ""));

describe("SideNav (ADR 0029)", () => {
  it("is Library · Join map · Import · Log, then the Settings sections, with the pending badge on Library", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "ann", role: "admin", kind: "human" } }),
      "GET /proposals/count": () => ({ body: { count: 3 } }),
    });
    renderNav();
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    await within(nav).findByText("Log");
    expect(names(nav)).toEqual(["Library", "Join map", "Import", "Log"]);
    expect(await within(nav).findByLabelText("3 pending")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /Library/ })).toContainElement(within(nav).getByLabelText("3 pending"));
    expect(within(nav).getByRole("link", { name: "Log" })).toHaveAttribute("href", "/log");
    expect(within(nav).queryByText("Audit")).not.toBeInTheDocument();
    const settings = screen.getByRole("navigation", { name: "Settings" });
    expect(names(settings)).toEqual(["Connections", "Team mode", "Users & roles", "Roles", "My tokens"]);
  });

  it("hides what the token can't use", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human", tools: ["search", "get_definition"] } }),
    });
    renderNav();
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    const settings = await screen.findByRole("navigation", { name: "Settings" });
    expect(names(nav)).toEqual(["Library"]);
    expect(names(settings)).toEqual(["My tokens"]);
  });
});
