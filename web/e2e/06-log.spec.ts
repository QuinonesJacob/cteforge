import { api, expect, screenshot, signIn, test, tokens } from "./helpers";

// ADR 0029: the audit log is the Log. Runs come from agents (MCP) and scheduled jobs (API /
// CLI), never from the GUI: an agent runs three definitions through the API first.
test("the Log lists real call names with trust and outcome; filters narrow it; a row expands", async ({ page }) => {
  for (const id of ["realization_rate", "invoices_by_status", "booked_vs_collected"]) {
    const run = await api("POST", "/run", tokens.agent, { uses: { [id]: { id } }, values: {}, run: true, limit: 5, question: `E2E ${id}?` });
    expect(run.status).toBe(200);
  }

  await signIn(page, tokens.admin, "/audit"); // the old link still works
  await expect(page).toHaveURL(/\/log$/);
  await expect(page.getByRole("heading", { level: 1, name: "Log" })).toBeVisible();
  const table = page.getByRole("region", { name: "Log events" });
  const rows = table.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  // The summary counts only what was returned.
  await expect(page.locator(".log-summary")).toContainText(/\d+ agents?/);
  await expect(page.locator(".log-summary")).toContainText(/\d+ (person|people)/);
  // Real call names: kind + op.
  await expect(table.getByRole("button", { name: /^propose · / }).first()).toBeVisible();
  await screenshot(page, "06-log");

  // Kind: only compile_and_run events, each with a trust icon and a compile ID.
  await page.getByLabel("Kind").selectOption("compile_and_run");
  await page.getByRole("button", { name: "Apply" }).click();
  let kinds: string[] = [];
  await expect(async () => {
    kinds = await rows.locator("td:nth-child(3)").allTextContents();
    expect(new Set(kinds)).toEqual(new Set(["compile_and_run"]));
  }).toPass();
  expect(kinds.length).toBeGreaterThanOrEqual(3);
  const labels = await rows.locator("td:nth-child(5) [role=img]").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
  expect(labels).toContain("LLM annotated");
  expect(labels).toContain("Verified");
  for (const id of await rows.locator("td:nth-child(9)").allTextContents()) expect(id).toMatch(/^\w{4,}$/);
  await expect(rows.first().locator("td:nth-child(6)")).toContainText("ok");

  // A row expands to the values, question, token prefix (never the token) and connection.
  await rows.first().getByRole("button", { name: "compile_and_run" }).click();
  const detail = table.locator("tr.row-detail");
  await expect(detail).toContainText("Question");
  await expect(detail).toContainText(/cfg_pat_\w+…/);
  await expect(detail).not.toContainText(tokens.agent);
  await expect(detail).toContainText("Connection");
  await expect(detail.getByRole("button", { name: "Copy compile ID" })).toBeVisible();
  await screenshot(page, "06-log-runs");

  // Definition ID narrows to the imported definition's runs.
  await page.getByLabel("Definition ID").fill("invoices_by_status");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("invoices_by_status");
  await expect(rows.first().getByRole("img", { name: "LLM annotated" })).toBeVisible();

  // Actor: ben's events only (his approval in strict mode).
  await page.getByRole("button", { name: "Clear" }).click();
  await page.getByLabel("Actor").fill("ben");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(async () => {
    const actors = await rows.locator("td:nth-child(2)").allTextContents();
    expect(actors.length > 0 && actors.every((a) => a.includes("ben"))).toBe(true);
  }).toPass();
  const benCalls = await rows.locator("td:nth-child(3)").allTextContents();
  expect(benCalls.some((c) => c.startsWith("review"))).toBe(true);

  // An actor with no events shows the empty state.
  await page.getByLabel("Actor").fill("nobody-at-all");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("No events match")).toBeVisible();
});
