import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import { mockApi } from "../test/fetchMock";
import { SettingsPage } from "./SettingsPage";

afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  render(
    <MemoryRouter>
      <ToastProvider>
        <AuthProvider>
          <SettingsPage />
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("SettingsPage", () => {
  it("tells non-admins it is admin-only, without calling admin routes", async () => {
    const api = mockApi({ "GET /me": () => ({ body: { name: "vic", role: "viewer", kind: "human" } }) });
    renderPage();
    expect(await screen.findByText("Settings and users are for admins only.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Default connection")).not.toBeInTheDocument();
    expect(api.calls.some((c) => ["/users", "/settings", "/profiles"].includes(c.path))).toBe(false);
  });

  it("lets admins set and clear the default connection", async () => {
    const user = userEvent.setup();
    let stored: string | null = null;
    const settings = () => ({
      mode: "open",
      default_connection: stored,
      effective_default_connection: stored ?? "wh",
      issues: [],
    });
    const api = mockApi({
      "GET /me": () => ({ body: { name: "root", role: "admin", kind: "human" } }),
      "GET /settings": () => ({ body: settings() }),
      "PUT /settings": (body) => {
        stored = (body as { default_connection: string | null }).default_connection;
        return { body: settings() };
      },
      "GET /users": () => ({ body: [{ name: "root", role: "admin", kind: "human" }] }),
      "GET /profiles": () => ({
        body: [
          { name: "wh", dialect: "duckdb", default: stored === null || stored === "wh" },
          { name: "rs", dialect: "postgres", default: stored === "rs" },
        ],
      }),
    });
    renderPage();
    expect(await screen.findByRole("heading", { name: "Team mode" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    const select = await screen.findByLabelText("Default connection");
    await waitFor(() => expect(select).toBeEnabled());
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "None" })).toBeInTheDocument();
    expect(screen.getByText("Not set: wh is used (server configuration).")).toBeInTheDocument();

    await user.selectOptions(select, "rs (postgres)");
    await waitFor(() => expect(select).toHaveValue("rs"));
    expect(api.calls.find((c) => c.method === "PUT")!.body).toEqual({ default_connection: "rs" });

    await waitFor(() => expect(select).toBeEnabled());
    await user.selectOptions(select, "None");
    await waitFor(() => expect(api.calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    expect(api.calls.filter((c) => c.method === "PUT").at(-1)!.body).toEqual({ default_connection: null });
    await waitFor(() => expect(select).toHaveValue(""));
  });

  it("warns when the stored default connection is no longer configured", async () => {
    mockApi({
      "GET /me": () => ({ body: { name: "root", role: "admin", kind: "human" } }),
      "GET /settings": () => ({
        body: {
          mode: "open",
          default_connection: "gone",
          effective_default_connection: "wh",
          issues: [
            {
              code: "default_connection_missing",
              severity: "warning",
              message: "The default connection 'gone' is no longer configured; 'wh' is used instead.",
            },
          ],
        },
      }),
      "GET /users": () => ({ body: [] }),
      "GET /profiles": () => ({ body: [{ name: "wh", dialect: "duckdb", default: true }] }),
    });
    renderPage();
    expect(await screen.findByText(/'gone' is no longer configured/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Default connection")).toHaveValue("");
  });
});
