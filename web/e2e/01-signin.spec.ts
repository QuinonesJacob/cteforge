import { expect, screenshot, signIn, test, tokens } from "./helpers";

test("a wrong token shows an error and stays on sign-in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await screenshot(page, "01-signin");
  await page.getByLabel("API token").fill("not-a-real-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Invalid or missing token");
  await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(0);
  await screenshot(page, "01-signin-error");
});

test("the admin token signs in; the sidebar and the user menu show the admin", async ({ page }) => {
  await signIn(page, tokens.admin);
  const nav = page.getByRole("navigation", { name: "Primary" });
  // ADR 0029: Library · Join map · Import · Log, then the Settings sections.
  await expect(nav.getByRole("link")).toHaveText([/^Library/, "Join map", "Import", "Log"]);
  await expect(nav.getByRole("link", { name: "Explore" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Audit" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Settings" }).getByRole("link")).toHaveText([
    "Connections",
    "Team mode",
    "Users & roles",
    "Roles",
    "My tokens",
  ]);
  const userButton = page.getByRole("button", { name: "Your account: admin" });
  await userButton.click();
  const menu = page.getByRole("group", { name: "Account" });
  await expect(menu).toContainText("admin");
  await menu.getByRole("button", { name: /Your tools/ }).click();
  await expect(menu.getByRole("list", { name: "Your tools" })).toContainText("manage_settings");
  await expect(menu.getByRole("group", { name: "Theme" })).toBeVisible();
  await screenshot(page, "01-user-menu");
  await menu.getByRole("link", { name: "Personal access tokens" }).click();
  await expect(page).toHaveURL(/\/settings\/tokens$/);
  // The token survives a reload of the tab (sessionStorage).
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
});

test("signing out returns to the sign-in form", async ({ page }) => {
  await signIn(page, tokens.admin);
  await page.getByRole("button", { name: /^Your account/ }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
