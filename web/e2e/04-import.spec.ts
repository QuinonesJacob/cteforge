import { api, expect, screenshot, signIn, test, tokens } from "./helpers";

const SQL = `SELECT status, COUNT(*) AS n
FROM invoices
WHERE invoiced_at >= DATE '2026-01-01'
  AND invoiced_at < DATE '2026-04-01'
  AND status <> 'void'
GROUP BY status`;

test("import a trusted query, build variables, auto-apply, then see its overview", async ({ page }) => {
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
  const variables = page.getByRole("region", { name: "Variables", exact: true });
  await expect(variables.locator(".draft-item")).toHaveCount(2);
  // ADR 0029: the SQL with its values underlined; the variable's values highlighted.
  const sqlPanel = page.locator(".marked-sql");
  await expect(sqlPanel).toContainText("Values are underlined");
  await expect(sqlPanel.locator(".val-variable")).toHaveText(["'2026-01-01'", "'2026-04-01'"]);
  await expect(page.locator(".stepper [aria-current=step]")).toContainText("Make variables");
  await screenshot(page, "04-import-variables");
  await page.getByRole("button", { name: "Next: describe meaning →" }).click();

  // Step 3: describe it. Continue without a meaning shows the error first.
  await page.getByLabel(/^Title/).fill("Invoices by status");
  // ADR 0024: the ID is filled in from the title and locked until "Edit ID".
  const idField = page.getByLabel(/^ID/);
  await expect(idField).toHaveValue("invoices_by_status");
  await expect(idField).toHaveAttribute("readonly", "");
  await expect(page.getByText("Filled in from the title. Agents and composed definitions use this name.")).toBeVisible();
  await page.getByRole("button", { name: "Edit ID" }).click();
  await expect(idField).not.toHaveAttribute("readonly", "");
  await page.getByRole("button", { name: "Use title" }).click();
  await expect(idField).toHaveAttribute("readonly", "");
  await expect(idField).toHaveValue("invoices_by_status");
  await page.getByRole("button", { name: "Next: review and submit →" }).click();
  await expect(page.locator("#md-meaning-error")).toBeVisible();
  await page.getByLabel(/^Meaning/).fill("Number of non-void invoices per invoice status in a period.");
  await page.getByLabel("Keywords").fill("invoice count, invoices by status");
  await page.getByLabel("Grain").fill("one row per invoice status");
  await page.getByLabel("Proposal summary").fill("Import invoices by status");
  await screenshot(page, "04-import-describe");
  await page.getByRole("button", { name: "Next: review and submit →" }).click();

  // Step 4: review and submit; the team is in open mode, so it applies at once.
  await expect(page.getByRole("region", { name: "Review before submitting" })).toContainText("2 variables (1 changeable)");
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect(page.getByRole("heading", { name: "Imported" })).toBeVisible();
  await expect(page.getByText("auto-applied", { exact: true })).toBeVisible();
  await screenshot(page, "04-import-result");

  await page.getByRole("link", { name: /Open definition invoices_by_status v1/ }).click();
  await expect(page).toHaveURL(/\/definitions\/invoices_by_status$/);
  await expect(page.getByRole("heading", { level: 1, name: "Invoices by status" })).toBeVisible();
  // ADR 0029: no Run tab; the variables agents can fill, with their original values.
  await expect(page.getByRole("tab", { name: "Run" })).toHaveCount(0);
  const vars = page.getByRole("region", { name: "Variables", exact: true });
  await expect(vars.getByRole("row", { name: /period/ })).toContainText("2026-01-01 → 2026-04-01");
  await expect(vars.getByRole("row", { name: /excluded_status/ })).toContainText("fixed");
  await expect(vars.getByRole("row", { name: /excluded_status/ })).toContainText("void");
  await screenshot(page, "04-imported-definition");
});

test("admin sets the default connection in Settings; Import starts on it", async ({ page }) => {
  try {
    await signIn(page, tokens.admin, "/settings/connections");
    const select = page.getByLabel("Default connection");
    await expect(select).toBeEnabled();
    await expect(select.locator("option")).toHaveText(["None", "demo (duckdb)"]);
    await select.selectOption("demo");
    await expect(page.getByText("Default connection set to demo")).toBeVisible();
    await expect(select).toHaveValue("demo");
    // ADR 0029: the header shows the default connection.
    await expect(page.getByRole("banner")).toContainText("demo default connection");
    await screenshot(page, "04-settings-default-connection");

    await page.getByRole("link", { name: "Import", exact: true }).click();
    await expect(page).toHaveURL(/\/import$/);
    await expect(page.getByLabel("Warehouse profile")).toHaveValue("demo");
    const dialect = page.getByLabel("Dialect", { exact: true });
    await expect(dialect).toHaveValue("duckdb");
    await expect(page.getByText("From connection demo.")).toBeVisible();
    // Still overridable per import, with the cost spelled out.
    await dialect.selectOption("postgres");
    await expect(
      page.getByText("Differs from connection demo: checks run on SQL translated to duckdb, lower trust."),
    ).toBeVisible();

    // Non-admins see Settings for their own tokens only (team settings need manage_settings).
    await page.evaluate(() => sessionStorage.clear()); // sign out (the token lives there)
    await signIn(page, tokens.approver);
    await expect(page.getByRole("link", { name: "Import", exact: true })).toBeVisible();
    const settings = page.getByRole("navigation", { name: "Settings" });
    await expect(settings.getByRole("link")).toHaveText(["My tokens"]);
    await settings.getByRole("link", { name: "My tokens" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "My tokens" })).toBeVisible();
    await page.goto("/settings/connections"); // not offered: lands on My tokens
    await expect(page.getByRole("heading", { level: 1, name: "My tokens" })).toBeVisible();
    await expect(page.getByLabel("Default connection")).toHaveCount(0);
  } finally {
    await api("PUT", "/settings", tokens.admin, { default_connection: null });
  }
});
