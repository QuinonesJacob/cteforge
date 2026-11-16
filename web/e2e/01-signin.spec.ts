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

test("the admin token signs in; the nav shows the admin", async ({ page }) => {
  await signIn(page, tokens.admin);
  const nav = page.getByRole("navigation", { name: "Primary" });
  for (const link of ["Library", "Import", "Review", "Audit", "Settings"])
    await expect(nav.getByRole("link", { name: link, exact: true })).toBeVisible();
  const userButton = page.getByRole("button", { name: /admin/ });
  await expect(userButton).toContainText("admin");
  await userButton.click();
  await expect(page.getByRole("group", { name: "Account" })).toContainText("admin · human");
  // The token survives a reload of the tab (sessionStorage).
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
});

test("signing out returns to the sign-in form", async ({ page }) => {
  await signIn(page, tokens.admin);
  await page.getByRole("button", { name: /admin/ }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
