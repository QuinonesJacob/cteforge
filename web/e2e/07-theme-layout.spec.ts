import { expect, hasHorizontalScroll, screenshot, signIn, test, tokens, type Page } from "./helpers";

const bodyBackground = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test("the theme toggle cycles system → light → dark and is remembered", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await signIn(page, tokens.admin);
  const html = page.locator("html");
  const toggle = page.getByRole("button", { name: /^Theme: / });
  await expect(toggle).toHaveAccessibleName(/Theme: System/);
  await expect(html).not.toHaveAttribute("data-theme");
  const systemLight = await bodyBackground(page);

  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "light");
  await expect(toggle).toHaveAccessibleName(/Theme: Light/);
  expect(await bodyBackground(page)).toBe(systemLight);

  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toHaveAccessibleName(/Theme: Dark/);
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
  await page.getByRole("button", { name: /^Theme: / }).click();
  await expect(html).not.toHaveAttribute("data-theme");
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await bodyBackground(page)).toBe(dark);
  await page.emulateMedia({ colorScheme: "light" });
  expect(await bodyBackground(page)).toBe(systemLight);
});

test.describe("at 375px wide", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("Library, Definition and other pages have no horizontal scroll", async ({ page }) => {
    await signIn(page, tokens.admin);
    await expect(page.locator("article.hit").first()).toBeVisible();
    expect(await hasHorizontalScroll(page)).toBe(false);
    await screenshot(page, "07-library-375");

    // The primary nav collapses behind a menu button.
    const menu = page.getByRole("button", { name: "Open menu" });
    await expect(menu).toBeVisible();
    await menu.click();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Audit" })).toBeVisible();
    await screenshot(page, "07-nav-375");
    await page.getByRole("button", { name: "Close menu" }).click();

    await page.getByRole("link", { name: "Realization Rate (monthly, by region)" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await hasHorizontalScroll(page)).toBe(false);
    await screenshot(page, "07-definition-375");

    await page.getByRole("tab", { name: "Run" }).click();
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("region", { name: "Run result" })).toBeVisible();
    expect(await hasHorizontalScroll(page)).toBe(false);
    await screenshot(page, "07-definition-run-375");

    for (const path of ["/joins", "/?review=mine", "/audit", "/import", "/settings"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect(await hasHorizontalScroll(page), `${path} scrolls sideways`).toBe(false);
      await screenshot(page, `07${path.replace("/", "-").replace(/[?=]/g, "-")}-375`);
    }
  });
});
