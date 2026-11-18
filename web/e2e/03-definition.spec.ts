import { expect, screenshot, signIn, test, tokens } from "./helpers";

// ADR 0029: the definition page has no Run tab (agents run through MCP; scheduled runs use
// the API or CLI). It shows what agents can fill, the trusted SQL and the facts around it.
test("the definition overview: usage notes, variables with original values, trusted SQL, facts", async ({ page }) => {
  await signIn(page, tokens.admin, "/definitions/realization_rate");
  await expect(page.getByRole("heading", { level: 1, name: "Realization Rate (monthly, by region)" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Library / realization_rate");
  const tabs = page.getByRole("tablist", { name: "Definition sections" }).getByRole("tab");
  await expect(tabs).toHaveText(["Overview", /^History · \d+ versions?$/, "Edit"]);
  await expect(page.getByRole("tab", { name: "Run" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Run it/ })).toHaveCount(0);
  await expect(page.locator(".def-state")).toContainText(/not verified by a person yet|Verified by/);

  await expect(page.getByRole("heading", { name: "Use when", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Don’t use when" })).toBeVisible();
  const vars = page.getByRole("region", { name: "Variables", exact: true });
  await expect(vars.getByRole("columnheader")).toHaveText(["Name", "Type", "Original value", "Allowed"]);
  await expect(vars.getByRole("row", { name: /regions/ })).toBeVisible();

  // The trusted SQL is collapsed until opened.
  const sql = page.locator("details.sql-details");
  await expect(sql.locator("summary")).toHaveText(/^Trusted SQL · \d+ lines$/);
  await expect(sql.getByRole("button", { name: "Copy SQL" })).toBeHidden();
  await sql.locator("summary").click();
  await expect(sql.getByRole("button", { name: "Copy SQL" })).toBeVisible();

  const facts = page.getByRole("complementary", { name: "Facts" });
  for (const label of ["Grain", "Outputs", "Joins", "Used by", "Keywords", "Owner"]) await expect(facts.getByText(label, { exact: true })).toBeVisible();
  await expect(facts.getByRole("link", { name: "See in Join map" })).toHaveAttribute("href", "/joins?definition=realization_rate");
  await screenshot(page, "03-definition");

  // "Propose a change" opens the Edit flow that exists.
  await page.getByRole("button", { name: "Propose a change" }).click();
  await expect(page.getByRole("tab", { name: "Edit" })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/tab=edit/);
  // An old ?tab=run link lands on the overview.
  await page.goto("/definitions/realization_rate?tab=run");
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: /^History/ }).click();
  await expect(page.getByRole("region", { name: "Version history" })).toBeVisible();
});

test("See in Join map opens the joins of that definition", async ({ page }) => {
  await signIn(page, tokens.admin, "/definitions/realization_rate");
  await page.getByRole("link", { name: "See in Join map" }).click();
  await expect(page).toHaveURL(/\/joins\?definition=realization_rate$/);
  await expect(page.getByText("Definition:")).toBeVisible();
  await expect(page.locator("tr[data-edge]").first()).toBeVisible();
});
