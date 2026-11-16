import { expect, screenshot, signIn, test, tokens } from "./helpers";

test("the library lists the three demo definitions, filters by search, opens one", async ({ page }) => {
  await signIn(page, tokens.admin);
  const hits = page.locator("article.hit");
  await expect(hits).toHaveCount(3);
  await expect(page.getByText("3 definitions")).toBeVisible();
  await screenshot(page, "02-library");

  const search = page.getByRole("searchbox", { name: "Search definitions" });
  // "collected" ranks the definition named for it first (all three mention collected revenue).
  await search.fill("collected");
  await expect(page).toHaveURL(/[?&]q=collected/);
  await expect(hits.first().locator(".hit-title")).toHaveText(/Booked vs\.? collected/i);
  // A narrower term filters the list down.
  await search.fill("grain");
  await expect(page).toHaveURL(/[?&]q=grain/);
  await expect(hits).toHaveCount(1);
  await expect(page.getByText("1 definition", { exact: true })).toBeVisible();
  await screenshot(page, "02-library-search");

  await search.fill("");
  await expect(hits).toHaveCount(3);
  await page.getByRole("link", { name: "Realization Rate (monthly, by region)" }).click();
  await expect(page).toHaveURL(/\/definitions\/realization_rate$/);
  await expect(page.getByRole("heading", { level: 1, name: "Realization Rate (monthly, by region)" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  await screenshot(page, "02-definition-overview");
});

test("a search with no match says so", async ({ page }) => {
  await signIn(page, tokens.admin, "/?q=zzzunmatchedzzz");
  await expect(page.getByText("Nothing matches “zzzunmatchedzzz”")).toBeVisible();
});
