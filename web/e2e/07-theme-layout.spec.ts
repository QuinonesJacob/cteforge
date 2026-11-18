import { expect, hasHorizontalScroll, screenshot, signIn, test, tokens, type Page } from "./helpers";

const bodyBackground = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);

/** The theme control lives in the user menu (ADR 0029). */
async function pickTheme(page: Page, name: "System theme" | "Light theme" | "Dark theme") {
  const account = page.getByRole("button", { name: /^Your account/ });
  if ((await account.getAttribute("aria-expanded")) !== "true") await account.click();
  const button = page.getByRole("group", { name: "Theme" }).getByRole("button", { name });
  await button.click();
  await expect(button).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
}

test("the theme follows the system, or light / dark from the user menu, and is remembered", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await signIn(page, tokens.admin);
  const html = page.locator("html");
  await expect(html).not.toHaveAttribute("data-theme");
  const systemLight = await bodyBackground(page);
  expect(systemLight).toBe("rgb(237, 235, 228)"); // the warm neutral background

  await pickTheme(page, "Light theme");
  await expect(html).toHaveAttribute("data-theme", "light");
  expect(await bodyBackground(page)).toBe(systemLight);

  await pickTheme(page, "Dark theme");
  await expect(html).toHaveAttribute("data-theme", "dark");
  const dark = await bodyBackground(page);
  expect(dark).not.toBe(systemLight);
  await screenshot(page, "07-library-dark");

  // Remembered across a reload, and wins over the system preference.
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await page.getByRole("link", { name: "Realization Rate (monthly, by region)" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Realization Rate (monthly, by region)" })).toBeVisible();
  await screenshot(page, "07-definition-dark");

  // Back to system: follows prefers-color-scheme (dark here).
  await pickTheme(page, "System theme");
  await expect(html).not.toHaveAttribute("data-theme");
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await bodyBackground(page)).toBe(dark);
  await page.emulateMedia({ colorScheme: "light" });
  expect(await bodyBackground(page)).toBe(systemLight);
});

test.describe("at 375px wide", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("every page works without horizontal scroll", async ({ page }) => {
    await signIn(page, tokens.admin);
    await expect(page.locator("article.hit").first()).toBeVisible();
    expect(await hasHorizontalScroll(page)).toBe(false);
    await screenshot(page, "07-library-375");

    // The sidebar collapses behind a menu button.
    const menu = page.getByRole("button", { name: "Open menu" });
    await expect(menu).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();
    await menu.click();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Log" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Settings" }).getByRole("link", { name: "My tokens" })).toBeVisible();
    await screenshot(page, "07-nav-375");
    await page.getByRole("button", { name: "Close menu" }).click();

    await page.getByRole("link", { name: "Realization Rate (monthly, by region)" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Realization Rate (monthly, by region)" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Facts" })).toBeVisible();
    expect(await hasHorizontalScroll(page)).toBe(false);
    await screenshot(page, "07-definition-375");

    for (const path of ["/joins", "/?review=mine", "/log", "/import", "/settings/users", "/settings/tokens"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect(await hasHorizontalScroll(page), `${path} scrolls sideways`).toBe(false);
      await screenshot(page, `07-${path.replace(/^\/\??/, "").replace(/[/?=&]/g, "-")}-375`);
    }
  });
});
