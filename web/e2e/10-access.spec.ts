// Roles, personal access tokens and per-token tools (ADR 0027).
import { api, expect, screenshot, signIn, test, tokens, type Page } from "./helpers";

function section(page: Page, heading: string) {
  return page.locator("section.card", { has: page.getByRole("heading", { name: heading, exact: true }) });
}

async function revealedToken(page: Page): Promise<string> {
  const value = page.getByLabel("Token", { exact: true });
  await expect(value).toBeVisible();
  const token = (await value.textContent())?.trim() ?? "";
  expect(token).toMatch(/^cfg_pat_[A-Za-z0-9_-]{43}$/);
  await expect(page.getByRole("button", { name: "Copy token" })).toBeVisible();
  return token;
}

test.beforeEach(({ page }) => {
  page.on("dialog", (d) => void d.accept());
});

test("admin creates a custom role in Settings → Roles", async ({ page }) => {
  await signIn(page, tokens.admin, "/settings");
  const roles = section(page, "Roles");
  await expect(roles.getByRole("heading", { name: /^admin/ })).toBeVisible();
  // The admin role's Allowed list is locked: every tool, not editable.
  await roles.getByRole("button", { name: "Edit role admin" }).click();
  const adminForm = roles.getByRole("form", { name: "Role admin" });
  const adminSearch = adminForm.getByRole("group", { name: "Allowed (always every tool): Use", exact: true }).getByLabel("search");
  await expect(adminSearch).toBeChecked();
  await expect(adminSearch).toBeDisabled();
  await roles.getByRole("button", { name: "Close role admin" }).click();

  await roles.getByRole("button", { name: "New role" }).click();
  const form = roles.getByRole("form", { name: "New role" });
  await form.getByLabel("Role name").fill("support_bot");
  await form.getByLabel("Description").fill("Search and run only");
  const allowedUse = form.getByRole("group", { name: "Allowed: Use", exact: true });
  for (const tool of ["search", "get definition", "compile and run"]) await allowedUse.getByLabel(tool).check();
  await expect(form.getByRole("group", { name: "Allowed: Admin", exact: true }).getByLabel("manage user")).toBeDisabled();
  await form.getByRole("group", { name: "On by default: Use", exact: true }).getByLabel("search").check();
  await screenshot(page, "10-roles-new");
  await form.getByRole("button", { name: "Create role" }).click();
  await expect(page.getByText("Role support_bot created")).toBeVisible();
  await expect(roles.getByRole("heading", { name: "support_bot" })).toBeVisible();
  const listed = await api<{ name: string; allowed: string[]; defaults: string[] }[]>("GET", "/roles", tokens.admin);
  const role = listed.json.find((r) => r.name === "support_bot");
  expect(role?.allowed).toEqual(["search", "get_definition", "compile_and_run"]);
  expect(role?.defaults).toEqual(["search"]);
});

test("a person generates a token in My tokens, signs in with it, and revokes it", async ({ page }) => {
  await signIn(page, tokens.approver, "/settings");
  const mine = section(page, "My tokens");
  await mine.getByRole("button", { name: "Generate token" }).click();
  await mine.getByLabel("Token name").fill("e2e laptop");
  await mine.getByLabel("Only these tools").check();
  const use = mine.getByRole("group", { name: "Token tools: Use", exact: true });
  for (const tool of ["get definition", "compile and run", "check and run", "describe tables", "list joins"])
    await use.getByLabel(tool).uncheck();
  const build = mine.getByRole("group", { name: "Token tools: Build", exact: true });
  for (const tool of ["parse query", "propose definition", "revise definition", "propose join"])
    await build.getByLabel(tool).uncheck();
  // Admin tools are not offered: the approver role does not allow them.
  await expect(mine.getByLabel("manage user")).toHaveCount(0);
  await mine.getByRole("button", { name: "Generate token" }).click();
  const token = await revealedToken(page);
  await screenshot(page, "10-my-token-revealed");
  await page.getByRole("button", { name: "I have saved it" }).click();
  await expect(page.getByLabel("Token", { exact: true })).toHaveCount(0);
  const list = mine.getByRole("region", { name: "My token list" });
  await expect(list.getByRole("row", { name: /e2e laptop/ })).toContainText(token.slice(0, 12));

  // Its tools are exactly the ones picked, over the API too.
  const me = await api<{ tools: string[] }>("GET", "/me", token);
  expect(me.json.tools).toEqual(["search", "list_proposals", "review_proposal", "verify", "view_audit"]);
  expect((await api("POST", "/check", token, { sql: "SELECT 1", run: false, limit: 1 })).status).toBe(403);

  // Sign in with it: the nav follows its tools (no Import without the build tools).
  await page.evaluate(() => sessionStorage.clear());
  await signIn(page, token, "/settings");
  await expect(page.getByRole("link", { name: "Import", exact: true })).toHaveCount(0);
  // ADR 0026: no list_joins → no Join map; list_proposals → the Library carries the badge.
  await expect(page.getByRole("link", { name: "Join map", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /^Library/ })).toBeVisible();
  const row = section(page, "My tokens").getByRole("row", { name: /e2e laptop/ });
  await expect(row.getByText("this session")).toBeVisible();
  await screenshot(page, "10-my-tokens");
  // Revoking the token in use signs this tab out on its next call.
  await row.getByRole("button", { name: "Revoke token e2e laptop" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect((await api("GET", "/me", token)).status).toBe(401);
});

test("admin creates an agent, generates its token in Settings → Users, and revokes it", async ({ page }) => {
  await signIn(page, tokens.admin, "/settings");
  const users = section(page, "Users");
  await users.locator("#cu-name").fill("support-bot");
  await users.locator("#cu-role").selectOption("support_bot");
  await users.getByLabel("agent", { exact: true }).check();
  await users.getByRole("button", { name: "Create user" }).click();
  const first = await revealedToken(page); // the "default" token, shown once
  await page.getByRole("button", { name: "I have saved it" }).click();
  expect((await api<{ tools: string[] }>("GET", "/me", first)).json.tools).toEqual(["search"]);

  const table = users.getByRole("region", { name: "Users" });
  const row = table.getByRole("row", { name: /support-bot/ }).first();
  await expect(row).toContainText("Use 3/6"); // the role allows 3 use tools
  await row.getByRole("button", { name: /tokens of support-bot/ }).click();
  await users.getByRole("button", { name: "Generate token for support-bot" }).click();
  await users.getByLabel("Token name").fill("ci");
  await users.getByRole("group", { name: "Token tools: Use", exact: true }).getByLabel("compile and run").check();
  await users.getByRole("button", { name: "Generate token for support-bot" }).click();
  const ci = await revealedToken(page);
  expect((await api<{ tools: string[] }>("GET", "/me", ci)).json.tools).toEqual(["search", "compile_and_run"]);
  await page.getByRole("button", { name: "I have saved it" }).click();
  const tokensTable = users.getByRole("region", { name: "Tokens of support-bot" });
  await expect(tokensTable.getByRole("row", { name: /ci/ })).toContainText(ci.slice(0, 12));
  await screenshot(page, "10-users-tokens");
  await tokensTable.getByRole("button", { name: "Revoke token ci" }).click();
  await expect(page.getByText('Token "ci" of support-bot revoked')).toBeVisible();
  expect((await api("GET", "/me", ci)).status).toBe(401);
  expect((await api("GET", "/me", first)).status).toBe(200);
  // Agents never get a token from the API (and the admin role edit stays human-only).
  expect((await api("POST", "/me/tokens", first, { name: "x", tool_mode: "follow_role", include: [], exclude: [] })).status).toBe(403);
});
