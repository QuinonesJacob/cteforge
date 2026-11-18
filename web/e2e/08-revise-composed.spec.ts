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
  await expect(page.getByRole("region", { name: "Variables", exact: true }).locator(".draft-item")).toHaveCount(2);
  await screenshot(page, "08-revise-variables");
  await page.getByRole("button", { name: "Next: describe meaning →" }).click();
  await expect(page.getByLabel(/^ID/)).toHaveValue("invoices_by_status");
  await page.getByLabel("Proposal summary").fill("Re-check invoices_by_status");
  await page.getByRole("button", { name: "Next: review and submit →" }).click();
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect(page.getByRole("heading", { name: "Revision applied" })).toBeVisible();
  await screenshot(page, "08-revise-result");
});

test("a composed definition shows its parts, variables and compiled SQL", async ({ page }) => {
  // The demo agent composed realization_with_booked from realization_rate (rr) and
  // booked_vs_collected (bc), both floating on their latest usable version (ADR 0026).
  await signIn(page, tokens.admin, "/definitions/realization_with_booked");
  await expect(page.getByRole("heading", { level: 1, name: /Realization rate next to booked revenue/ })).toBeVisible();
  const parts = page.getByRole("heading", { name: "Parts" }).locator("..");
  await expect(parts.getByRole("link", { name: "realization_rate" })).toBeVisible();
  await expect(parts.getByRole("link", { name: "booked_vs_collected" })).toBeVisible();
  await expect(parts.getByText(/^latest/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Compiled SQL" })).toBeVisible();
  await expect(page.getByLabel(/full query with original values/)).toContainText("rr__");
  await expect(page.getByRole("region", { name: "Variables", exact: true })).toContainText("rr.period");
  await expect(page.getByRole("list", { name: "Joins of realization_with_booked" })).toContainText("invoices");
  await screenshot(page, "08-composed-definition");

  // A part lists it under "Used by".
  await parts.getByRole("link", { name: "booked_vs_collected" }).click();
  await expect(page.getByRole("complementary", { name: "Facts" }).getByRole("link", { name: "realization_with_booked" })).toBeVisible();
  // No Run tab (ADR 0029): agents run it by id through MCP.
  await expect(page.getByRole("tab", { name: "Run" })).toHaveCount(0);
});
