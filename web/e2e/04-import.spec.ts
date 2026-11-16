import { expect, screenshot, signIn, test, tokens } from "./helpers";

const SQL = `SELECT status, COUNT(*) AS n
FROM invoices
WHERE invoiced_at >= DATE '2026-01-01'
  AND invoiced_at < DATE '2026-04-01'
  AND status <> 'void'
GROUP BY status`;

test("import a trusted query, build variables, auto-apply, then run it", async ({ page }) => {
  await signIn(page, tokens.admin);
  await page.getByRole("link", { name: "Import SQL" }).click();
  await expect(page).toHaveURL(/\/import$/);

  // Step 1: paste and parse against the demo profile (real column types).
  await page.getByLabel(/Trusted SQL/).fill(SQL);
  await page.getByLabel("Warehouse profile").selectOption("demo");
  await screenshot(page, "04-import-sql");
  await page.getByRole("button", { name: "Parse", exact: true }).click();

  // Step 2: literals with their compared types and structural-fact chips.
  const literals = page.getByRole("region", { name: "Literals" });
  await expect(literals.locator("tbody tr")).toHaveCount(3);
  const row = (id: string) =>
    literals.locator("tbody tr").filter({ has: page.getByRole("checkbox", { name: `Select literal ${id} `, exact: false }) });
  const start = row("main.L1");
  const end = row("main.L2");
  const status = row("main.L3");
  await expect(start).toContainText("'2026-01-01'");
  await expect(start).toContainText("TIMESTAMP");
  await expect(start).toContainText("plain filter");
  await expect(start).toContainText("Range pair with main.L2 (half open)");
  await expect(status).toContainText("'void'");
  await expect(status).toContainText("VARCHAR");
  await expect(status.getByRole("list", { name: "Structural facts" })).toContainText("negated");

  // A changeable date_range from the suggested pair.
  await start.getByRole("button", { name: "select pair" }).click();
  await expect(start.getByRole("checkbox")).toBeChecked();
  await expect(end.getByRole("checkbox")).toBeChecked();
  await page.getByRole("button", { name: "Make variable" }).click();
  const builder = page.getByRole("region", { name: "New variable" });
  await expect(builder.getByLabel("Type")).toHaveValue("date_range");
  await expect(builder).toContainText("Suggested range pair: shape half open is recorded.");
  await builder.getByLabel(/^Name/).fill("period");
  await builder.getByRole("checkbox", { name: /Changeable/ }).check();
  await builder.getByLabel(/^Description/).fill("Invoice date range (end exclusive).");
  await builder.getByRole("button", { name: "Add variable" }).click();
  await expect(start).toContainText("→ period");

  // A fixed text variable for the negated filter (fixed is the default role, ADR 0015).
  await status.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Make variable" }).click();
  await expect(builder.getByLabel("Type")).toHaveValue("text");
  await builder.getByLabel(/^Name/).fill("excluded_status");
  await expect(builder.getByRole("checkbox", { name: /Changeable/ })).not.toBeChecked();
  // Made changeable, a negated literal needs a description; fixed, it does not.
  await builder.getByRole("checkbox", { name: /Changeable/ }).check();
  await expect(builder).toContainText("Required because: main.L3 is negated");
  await builder.getByRole("checkbox", { name: /Changeable/ }).uncheck();
  await expect(builder).not.toContainText("Required because");
  await builder.getByLabel(/^Description/).fill("Void invoices never count.");
  await builder.getByRole("button", { name: "Add variable" }).click();
  await expect(page.getByRole("heading", { name: "Variables (2)" })).toBeVisible();
  await screenshot(page, "04-import-variables");
  await page.getByRole("button", { name: "Continue" }).click();

  // Step 3: describe it. Continue without a meaning shows the error first.
  await page.getByLabel(/^Title/).fill("Invoices by status");
  await expect(page.getByLabel(/^ID/)).toHaveValue("invoices_by_status");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.locator("#md-meaning-error")).toBeVisible();
  await page.getByLabel(/^Meaning/).fill("Number of non-void invoices per invoice status in a period.");
  await page.getByLabel("Keywords").fill("invoice count, invoices by status");
  await page.getByLabel("Grain").fill("one row per invoice status");
  await page.getByLabel("Proposal summary").fill("Import invoices by status");
  await screenshot(page, "04-import-describe");
  await page.getByRole("button", { name: "Continue" }).click();

  // Step 4: review and submit; the team is in open mode, so it applies at once.
  await expect(page.getByRole("region", { name: "Review before submitting" })).toContainText("2 variables (1 changeable)");
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect(page.getByRole("heading", { name: "Imported" })).toBeVisible();
  await expect(page.getByText("auto-applied", { exact: true })).toBeVisible();
  await screenshot(page, "04-import-result");

  await page.getByRole("link", { name: /Open definition invoices_by_status v1/ }).click();
  await expect(page).toHaveURL(/\/definitions\/invoices_by_status$/);
  await expect(page.getByRole("heading", { level: 1, name: "Invoices by status" })).toBeVisible();
  await page.getByRole("tab", { name: "Run" }).click();
  const period = page.getByRole("group").filter({ has: page.getByLabel("Start") }).first();
  await expect(period.getByLabel("Start")).toHaveValue("2026-01-01");
  await expect(period.getByLabel("End")).toHaveValue("2026-04-01");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.locator(".badge").first()).toContainText(/LLM annotated/i);
  const rows = result.locator("table tbody tr");
  await expect(rows.first()).toBeVisible();
  const statuses = await rows.locator("td:nth-child(2)").allTextContents();
  expect(statuses.length).toBeGreaterThan(0);
  expect(statuses).not.toContain("void");
  await expect(result.getByText(/\/\* cteforge: invoices_by_status v1 compile_id=\w+ \*\//)).toBeVisible();
  await screenshot(page, "04-imported-definition-run");
});
