import { expect, screenshot, signIn, test, tokens } from "./helpers";

// Runs after 04 (invoices_by_status exists, llm_annotated) and 05 (team back in open mode).
test.describe.configure({ mode: "serial" });

test("revise a definition's description from the Edit tab", async ({ page }) => {
  await signIn(page, tokens.admin, "/definitions/invoices_by_status");
  await expect(page.getByRole("heading", { level: 1, name: "Invoices by status" })).toBeVisible();
  await page.getByRole("tab", { name: "Edit" }).click();
  const propose = page.getByRole("button", { name: /^Propose v\d+$/ });
  await expect(propose).toBeDisabled();
  await expect(page.getByLabel(/^ID/)).toHaveAttribute("readonly", "");
  await page.getByLabel(/^Use when/).fill("Counting invoices per status in a period.");
  await screenshot(page, "08-definition-edit");
  const next = Number((await propose.textContent())?.replace(/\D/g, ""));
  await propose.click();
  // Open mode, unverified target, nothing to re-check: applied at once as the next version.
  await expect(page.getByRole("status").filter({ hasText: `Applied invoices_by_status v${next}` })).toBeVisible();
  await page.getByRole("tab", { name: "Overview" }).click();
  await expect(page.getByText("Counting invoices per status in a period.")).toBeVisible();
  await expect(page.getByLabel("Version")).toHaveValue(String(next));
});

test("revise SQL and variables in the import builder", async ({ page }) => {
  await signIn(page, tokens.admin, "/definitions/invoices_by_status?tab=edit");
  await page.getByRole("link", { name: /Change SQL or variables/ }).click();
  await expect(page).toHaveURL(/\/import\?revise=invoices_by_status$/);
  await expect(page.getByRole("heading", { level: 1, name: "Revise Invoices by status" })).toBeVisible();
  // The stored SQL (rebuilt from the template) and its variables are filled in.
  await expect(page.getByLabel(/Trusted SQL/)).toHaveValue(/status <> 'void'/);
  await page.getByRole("button", { name: "Back to variables" }).click();
  await expect(page.getByRole("heading", { name: "Variables (2)" })).toBeVisible();
  await screenshot(page, "08-revise-variables");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByLabel(/^ID/)).toHaveValue("invoices_by_status");
  await page.getByLabel("Proposal summary").fill("Re-check invoices_by_status");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect(page.getByRole("heading", { name: "Revision applied" })).toBeVisible();
  await screenshot(page, "08-revise-result");
});

test("compose definitions, save a recipe, run it, and browse the join map and tables", async ({ page }) => {
  await signIn(page, tokens.admin);
  await page.getByRole("link", { name: "Explore" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Explore" })).toBeVisible();

  // Compose: one definition by alias + a final SELECT over it.
  await page.getByRole("tab", { name: "Compose" }).click();
  await page.getByLabel("Add a definition").selectOption("booked_vs_collected");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("group", { name: "Alias booked_vs_collected" })).toBeVisible();
  await page.getByLabel(/Final SELECT/).fill("SELECT region, booked FROM booked_vs_collected ORDER BY region");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.locator("table tbody tr").first()).toBeVisible();
  await screenshot(page, "08-explore-compose");

  // Save as a recipe (open mode: applied at once), then run it from the Recipes tab.
  const save = page.getByRole("form", { name: "Save as recipe" });
  await save.getByLabel(/Recipe ID/).fill("booked_by_region");
  await save.getByLabel(/^Title/).fill("Booked by region");
  await save.getByRole("button", { name: "Propose recipe" }).click();
  await expect(page.getByRole("heading", { name: "Recipe saved" })).toBeVisible();

  await page.getByRole("tab", { name: "Recipes" }).click();
  const recipe = page.getByRole("region", { name: "Recipe booked_by_region" });
  await expect(recipe).toContainText("booked_vs_collected");
  await recipe.getByRole("button", { name: "Run", exact: true }).click();
  await expect(recipe.getByRole("region", { name: "Run result" }).locator("table tbody tr").first()).toBeVisible();
  await screenshot(page, "08-explore-recipes");

  // "Change values…" opens the recipe in the composer with its definitions.
  await recipe.getByRole("button", { name: /Change values/ }).click();
  await expect(page.getByRole("group", { name: "Alias booked_vs_collected" })).toBeVisible();
  await expect(page.getByLabel(/Final SELECT/)).toHaveValue(/FROM booked_vs_collected/);

  // Join map and tables.
  await page.getByRole("tab", { name: "Join map" }).click();
  await expect(page.getByRole("region", { name: "Join map" }).locator("tbody tr").first()).toBeVisible();
  await screenshot(page, "08-explore-joins");
  await page.getByRole("tab", { name: "Tables" }).click();
  const deals = page.getByRole("region", { name: "Table deals" });
  await expect(deals.getByRole("region", { name: "Columns of deals" })).toContainText("amount");
  await screenshot(page, "08-explore-tables");

  // Exploratory SQL: labeled by the join map.
  await page.getByRole("tab", { name: "SQL" }).click();
  await page.getByRole("textbox", { name: /^Exploratory SQL/ }).fill("SELECT region, COUNT(*) AS n FROM accounts GROUP BY region");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByRole("region", { name: "Run result" })).toContainText(/exploratory|new logic/i);
  await screenshot(page, "08-explore-sql");
});
