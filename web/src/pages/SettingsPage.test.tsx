import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../state/AuthContext";
import { ToastProvider } from "../state/ToastContext";
import type { TokenCard } from "../api/types";
import { ALL_TOOLS, BUILTIN_ROLE_TOOLS } from "../lib/tools";
import { mockApi } from "../test/fetchMock";
import { SettingsPage } from "./SettingsPage";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const VIEWER_TOOLS = BUILTIN_ROLE_TOOLS.viewer;
const VIC = {
  name: "vic",
  role: "viewer",
  kind: "human",
  active: true,
  tools: VIEWER_TOOLS,
  review_all: false,
  role_allowed: VIEWER_TOOLS,
  role_defaults: VIEWER_TOOLS,
};
const ROOT = { ...VIC, name: "root", role: "admin", tools: ALL_TOOLS, review_all: true, role_allowed: ALL_TOOLS, role_defaults: ALL_TOOLS };

function token(over: Partial<TokenCard> = {}): TokenCard {
  return {
    id: "t0",
    user_name: "vic",
    name: "default",
    prefix: "cfg_pat_abcd",
    created_at: "2026-11-17T00:00:00Z",
    created_by: "vic",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    tool_mode: "follow_role",
    include: [],
    exclude: [],
    tools: VIEWER_TOOLS,
    state: "active",
    ...over,
  };
}

const ROLES = [
  { name: "viewer", description: "", allowed: VIEWER_TOOLS, defaults: VIEWER_TOOLS, review_all: false, builtin: true, users: 1 },
  { name: "contributor", description: "", allowed: BUILTIN_ROLE_TOOLS.contributor, defaults: BUILTIN_ROLE_TOOLS.contributor, review_all: false, builtin: true, users: 0 },
  { name: "admin", description: "", allowed: ALL_TOOLS, defaults: ALL_TOOLS, review_all: true, builtin: true, users: 1 },
];

const SLOW = { timeout: 5000 };

/** A button by its full text (role queries are slow on this large page). */
function byButton(text: string): HTMLElement {
  const found = [...document.querySelectorAll("button")].find(
    (b) => (b.textContent ?? "").replace(/\s+/g, " ").trim() === text,
  );
  if (!found) throw new Error(`no button ${text}`);
  return found;
}

function formNamed(name: string): HTMLElement {
  const form = document.querySelector<HTMLElement>(`form[aria-label="${name}"]`);
  if (!form) throw new Error(`no form ${name}`);
  return form;
}

/** An admin's /me plus the routes every admin section loads. */
function adminRoutes() {
  return {
    "GET /me": () => ({ body: ROOT }),
    "GET /tools": () => ({ body: [] }),
    "GET /me/tokens": () => ({ body: [] }),
    "GET /roles": () => ({ body: ROLES }),
    "GET /settings": () => ({ body: { mode: "open", default_connection: null, effective_default_connection: null, issues: [] } }),
    "GET /profiles": () => ({ body: [] }),
  };
}

function renderPage(path = "/settings") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <AuthProvider>
          <Routes>
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/settings/:section" element={<SettingsPage />} />
          </Routes>
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("SettingsPage", () => {
  it("shows only My tokens to a viewer, without calling admin routes", async () => {
    const api = mockApi({
      "GET /me": () => ({ body: VIC }),
      "GET /tools": () => ({ body: [] }),
      "GET /me/tokens": () => ({ body: [token({ name: "default" })] }),
    });
    renderPage("/settings/users"); // not offered: lands on My tokens
    expect(await screen.findByRole("heading", { level: 1, name: "My tokens" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "My token list" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Default connection")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your profile" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Roles" })).not.toBeInTheDocument();
    expect(api.calls.some((c) => ["/users", "/settings", "/profiles", "/roles"].includes(c.path))).toBe(false);
  });

  it("generates a token shown once, limited to the role's tools, and revokes one", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let tokens = [token({ id: "t0", name: "default" })];
    const api = mockApi({
      "GET /me": () => ({ body: VIC }),
      "GET /tools": () => ({ body: [] }),
      "GET /me/tokens": () => ({ body: tokens }),
      "POST /me/tokens": (body) => {
        const made = token({ id: "t1", name: (body as { name: string }).name, prefix: "cfg_pat_NEW1" });
        tokens = [...tokens, made];
        return { status: 201, body: { token: "cfg_pat_NEW1secretsecret", info: made } };
      },
      "DELETE /me/tokens/t0": () => {
        tokens = tokens.map((t) => (t.id === "t0" ? { ...t, state: "revoked" as const, tools: [] } : t));
        return { body: tokens[0] };
      },
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Generate token" }));
    // Only the viewer role's tools are offered (no build or admin tools).
    expect(screen.getByLabelText("search")).toBeChecked();
    expect(screen.queryByLabelText("propose definition")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("manage user")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/Token name/), "Claude Code");
    await user.click(screen.getByLabelText("check and run"));
    await user.click(screen.getByRole("button", { name: "Generate token" }));
    expect(await screen.findByLabelText("Token")).toHaveTextContent("cfg_pat_NEW1secretsecret");
    expect(api.calls.find((c) => c.method === "POST")!.body).toEqual({
      name: "Claude Code",
      expires_at: null,
      tool_mode: "follow_role",
      include: [],
      exclude: ["check_and_run"],
    });
    await user.click(screen.getByRole("button", { name: "I have saved it" }));
    expect(screen.queryByLabelText("Token")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Revoke token default" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/me/tokens/t0")).toBe(true));
  });

  it("offers agents no way to generate tokens", async () => {
    mockApi({
      "GET /me": () => ({ body: { ...VIC, name: "bot", kind: "agent" } }),
      "GET /tools": () => ({ body: [] }),
      "GET /me/tokens": () => ({ body: [] }),
    });
    renderPage();
    expect(await screen.findByText(/generated by a person/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate token" })).not.toBeInTheDocument();
  });

  it("lets an admin change roles and active, see tokens and generate one for a user", { timeout: 20000 }, async () => {
    const user = userEvent.setup();
    const users = [
      { name: "root", role: "admin", kind: "human", active: true, tokens: [token({ user_name: "root" })] },
      { name: "vic", role: "viewer", kind: "human", active: true, tokens: [token({ id: "tv", prefix: "" })] },
    ];
    const api = mockApi({
      ...adminRoutes(),
      "GET /users": () => ({ body: users }),
      "PATCH /users/vic": (body) => ({ body: { ...users[1], ...(body as object) } }),
      "PATCH /users/root": (body) => ({ body: { ...users[0], ...(body as object) } }),
      "POST /users/vic/tokens": () => ({
        status: 201,
        body: { token: "cfg_pat_FORVICsecret", info: token({ id: "tn", user_name: "vic" }) },
      }),
    });
    renderPage("/settings/users");
    const role = await screen.findByLabelText("Role for vic");
    expect(screen.getByLabelText("Role for root")).toBeDisabled(); // not your own
    await user.selectOptions(role, "contributor");
    await waitFor(() => expect(api.calls.find((c) => c.method === "PATCH")!.body).toEqual({ role: "contributor" }));
    await user.click(byButton("1 active ▾ tokens of vic"));
    expect(within(screen.getByRole("region", { name: "Tokens of vic" })).getByText("legacy")).toBeInTheDocument();
    await user.click(byButton("Generate token for vic"));
    await user.type(screen.getByLabelText(/Token name/), "laptop");
    await user.click(byButton("Generate token for vic"));
    expect(await screen.findByLabelText("Token")).toHaveTextContent("cfg_pat_FORVICsecret");
    expect(api.calls.find((c) => c.path === "/users/vic/tokens")!.body).toMatchObject({ name: "laptop" });
    await user.click(screen.getByLabelText("vic is active"));
    await waitFor(() => expect(api.calls.filter((c) => c.method === "PATCH").at(-1)!.body).toEqual({ active: false }));
    // ADR 0029: an admin sets a user's display name and email.
    await user.click(byButton("Profile of root"));
    await user.type(screen.getByLabelText(/Display name/), "Root Admin");
    await user.type(screen.getByLabelText(/Email/), "root@example.com");
    await user.click(byButton("Save profile"));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PATCH" && c.path === "/users/root")!.body).toEqual({
        display_name: "Root Admin",
        email: "root@example.com",
      }),
    );
  });

  it("shows a user's display name and email, and the owner on their tokens", async () => {
    const user = userEvent.setup();
    mockApi({
      ...adminRoutes(),
      "GET /users": () => ({
        body: [{ name: "vic", role: "viewer", kind: "human", active: true, display_name: "Vic Tor", email: "vic@example.com", tokens: [token()] }],
      }),
    });
    renderPage("/settings/users");
    expect(await screen.findByText("Vic Tor")).toBeInTheDocument();
    expect(screen.getByText("vic@example.com")).toBeInTheDocument();
    await user.click(byButton("1 active ▾ tokens of vic"));
    const tokens = screen.getByRole("region", { name: "Tokens of vic" });
    expect(within(tokens).getByText(/Owner:/)).toHaveTextContent("Owner: Vic Tor (vic) · vic@example.com");
  });

  it("lets anyone set their own display name and email (PUT /me/profile)", async () => {
    const user = userEvent.setup();
    let me: Record<string, unknown> = { ...VIC };
    const api = mockApi({
      "GET /me": () => ({ body: me }),
      "GET /tools": () => ({ body: [] }),
      "GET /me/tokens": () => ({ body: [] }),
      "PUT /me/profile": (body) => {
        me = { ...me, ...(body as object) };
        return { body: me };
      },
    });
    renderPage("/settings/tokens");
    await user.type(await screen.findByLabelText(/Display name/), "Vic Tor");
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")!.body).toEqual({ display_name: "Vic Tor" }));
    await waitFor(() => expect(screen.getByLabelText(/Display name/)).toHaveValue("Vic Tor"));
  });

  it("configures roles: admin's Allowed is locked, admin-only tools only for admin", { timeout: 20000 }, async () => {
    const user = userEvent.setup();
    const api = mockApi({
      ...adminRoutes(),
      "GET /users": () => ({ body: [] }),
      "POST /roles": (body) => ({ status: 201, body: { ...(body as object), builtin: false, users: 0 } }),
    });
    renderPage("/settings/roles");
    await user.click(await waitFor(() => byButton("Edit role admin"), SLOW));
    const adminForm = formNamed("Role admin");
    const allowedSearch = within(adminForm).getAllByLabelText("search")[0];
    expect(allowedSearch).toBeChecked();
    expect(allowedSearch).toBeDisabled();
    await user.click(byButton("Close role admin"));
    await user.click(byButton("New role"));
    const form = formNamed("New role");
    await user.type(within(form).getByLabelText(/Role name/), "support_bot");
    const [allowSearch, defaultSearch] = within(form).getAllByLabelText("search");
    expect(within(form).getAllByLabelText("manage user")[0]).toBeDisabled();
    expect(defaultSearch).toBeDisabled(); // not allowed yet
    await user.click(allowSearch!);
    await user.click(within(form).getAllByLabelText("compile and run")[0]!);
    await user.click(within(form).getAllByLabelText("search")[1]!);
    await user.click(within(form).getByRole("button", { name: "Create role" }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "POST" && c.path === "/roles")!.body).toEqual({
        name: "support_bot",
        description: "",
        allowed: ["search", "compile_and_run"],
        defaults: ["search"],
        review_all: false,
      }),
    );
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
      ...adminRoutes(),
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
    renderPage(); // an admin lands on the first section, Connections
    expect(await screen.findByRole("heading", { name: "Default connection" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Connections" })).toBeInTheDocument();
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
      ...adminRoutes(),
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
    renderPage("/settings/connections");
    expect(await screen.findByText(/'gone' is no longer configured/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Default connection")).toHaveValue("");
  });
});
