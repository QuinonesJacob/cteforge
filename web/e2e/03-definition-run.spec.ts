import { expect, screenshot, signIn, test, tokens } from "./helpers";

test("run Realization Rate for Q3 2025 in APAC from the form", async ({ page }) => {
  await signIn(page, tokens.admin, "/definitions/realization_rate");
  await page.getByRole("tab", { name: "Run" }).click();
  await expect(page).toHaveURL(/tab=run/);

  const period = page.getByRole("group").filter({ has: page.getByLabel("Start") }).first();
  await period.getByLabel("Start").fill("2025-07-01");
  await period.getByLabel("End").fill("2025-10-01");

  // regions: a chips input pre-filled with the original values; replace them with APAC.
  const regions = page.locator(".run-var").filter({ has: page.locator("code", { hasText: /^regions$/ }) });
  const remove = regions.getByRole("button", { name: /^Remove / });
  while ((await remove.count()) > 0) await remove.first().click();
  await expect(regions.locator(".chip")).toHaveCount(0);
  await regions.getByRole("textbox").fill("APAC");
  await regions.getByRole("textbox").press("Enter");
  await expect(regions.locator(".chip")).toHaveText(["APAC×"]);

  await page.getByRole("button", { name: "Run", exact: true }).click();
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result).toBeVisible();
  await expect(result.locator(".badge").first()).toContainText(/LLM annotated/i);
  await expect(result.locator(".facts")).toContainText("Rows3");
  const rows = result.locator("table tbody tr");
  await expect(rows).toHaveCount(3);
  for (const row of await rows.all()) await expect(row).toContainText("APAC");
  await expect(result.getByText(/\/\* cteforge: realization_rate v\d+ compile_id=\w+ \*\//)).toBeVisible();
  const compileId = (await result.locator(".facts dd.mono").textContent())?.trim() ?? "";
  expect(compileId).toMatch(/^\w{4,}$/);
  await expect(result.getByText(new RegExp(`compile_id=${compileId}`))).toBeVisible();
  await screenshot(page, "03-definition-run");
});

test("an end date before the start date is refused with a clear message", async ({ page }) => {
  await signIn(page, tokens.admin, "/definitions/realization_rate?tab=run");
  const period = page.getByRole("group").filter({ has: page.getByLabel("Start") }).first();
  await period.getByLabel("Start").fill("2025-10-01");
  await period.getByLabel("End").fill("2025-07-01");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "End must not be before start." })).toBeVisible();
  await expect(page.getByRole("region", { name: "Run result" })).toHaveCount(0);
  await screenshot(page, "03-definition-run-invalid");
});
