import { expect, screenshot, signIn, test, tokens } from "./helpers";

test("the audit log lists the runs with their trust labels; filters narrow it", async ({ page }) => {
  await signIn(page, tokens.admin, "/audit");
  const table = page.getByRole("region", { name: "Audit events" });
  const rows = table.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  await screenshot(page, "06-audit");

  // Kind: only compile_and_run events, each with a trust label and a compile_id.
  await page.getByLabel("Kind").selectOption("compile_and_run");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(rows.locator("td:nth-child(3) code").first()).toHaveText("compile_and_run");
  const kinds = await rows.locator("td:nth-child(3) code").allTextContents();
  expect(new Set(kinds)).toEqual(new Set(["compile_and_run"]));
  expect(kinds.length).toBeGreaterThanOrEqual(3); // specs 03, 04 and 05 each ran once
  const labels = await rows.locator("td:nth-child(5)").allTextContents();
  expect(labels.some((l) => l.includes("LLM annotated"))).toBe(true);
  expect(labels.some((l) => l.includes("Verified"))).toBe(true);
  for (const id of await rows.locator("td:nth-child(9)").allTextContents()) expect(id).toMatch(/^\w{4,}$/);
  await screenshot(page, "06-audit-runs");

  // Definition ID narrows to the imported definition's runs.
  await page.getByLabel("Definition ID").fill("invoices_by_status");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("invoices_by_status");
  await expect(rows.first()).toContainText("LLM annotated");

  // Actor: ben's events only (his approval and his run in strict mode).
  await page.getByRole("button", { name: "Clear" }).click();
  await page.getByLabel("Actor").fill("ben");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(rows.first().locator("td:nth-child(2)")).toContainText("ben");
  const actors = await rows.locator("td:nth-child(2)").allTextContents();
  expect(actors.every((a) => a.startsWith("ben"))).toBe(true);
  const benKinds = await rows.locator("td:nth-child(3) code").allTextContents();
  expect(benKinds).toContain("review");

  // An actor with no events shows the empty state.
  await page.getByLabel("Actor").fill("nobody-at-all");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("No events match")).toBeVisible();
});
