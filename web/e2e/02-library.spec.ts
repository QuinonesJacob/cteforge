import { api, expect, screenshot, signIn, test, tokens } from "./helpers";

interface Outcome {
  proposal: { id: string; status: string };
  auto_applied: boolean;
}

test("the library lists the demo definitions (one composed by the agent), filters by words, opens one", async ({ page }) => {
  await signIn(page, tokens.admin);
  const rows = page.locator("article.hit");
  await expect(rows).toHaveCount(4);
  await expect(page.getByText("4 rows", { exact: true })).toBeVisible();
  const composed = rows.filter({ hasText: "Realization rate next to booked revenue" });
  await expect(composed.getByText("composed", { exact: true })).toBeVisible();
  await expect(composed).toContainText("Created by claude-agent (agent)");
  await screenshot(page, "02-library");

  const search = page.getByRole("searchbox", { name: "Search the library" });
  await search.fill("grain");
  await expect(page).toHaveURL(/[?&]q=grain/);
  await expect(rows).toHaveCount(1);
  await expect(rows.first().locator(".hit-title")).toHaveText(/by period grain/);
  await search.fill("zzzunmatchedzzz");
  await expect(page.getByText("Nothing matches these filters")).toBeVisible();

  await search.fill("");
  await expect(rows).toHaveCount(4);
  await page.getByRole("link", { name: "Realization Rate (monthly, by region)" }).click();
  await expect(page).toHaveURL(/\/definitions\/realization_rate$/);
  await expect(page.getByRole("heading", { level: 1, name: "Realization Rate (monthly, by region)" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  await screenshot(page, "02-definition-overview");
});

test("filters: pending approval, needs my review, created by, rejected hidden by default", async ({ page }) => {
  // An agent proposes a composed definition whose part does not exist: its checks fail, so it
  // waits (never auto-applied) as a row "Pending approval".
  const res = await api<Outcome>("POST", "/proposals/definitions", tokens.agent, {
    composition: { uses: { x: { id: "no_such_definition" } } },
    metadata: { id: "e2e_pending_combo", title: "E2E pending combo" },
    summary: "E2E: a pending composed definition",
  });
  expect(res.status).toBe(200);
  expect(res.json.proposal.status).toBe("pending");
  const proposalId = res.json.proposal.id;

  await signIn(page, tokens.admin);
  const rows = page.locator("article.hit");
  const pending = rows.filter({ hasText: "E2E pending combo" });
  await expect(pending.getByText("Pending approval")).toBeVisible();
  await expect(pending.getByRole("link", { name: "E2E pending combo" })).toHaveAttribute("href", `/review/${proposalId}`);
  // The pending count is on the Library nav item.
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: /^Library/ }).getByLabel(/pending/)).toBeVisible();

  // Needs my review: only what this person may approve.
  // The filters are controlled by the URL: click, then wait for the new state.
  await page.getByRole("checkbox", { name: "Needs my review" }).click();
  await expect(page).toHaveURL(/[?&]review=mine/);
  await expect(page.getByRole("checkbox", { name: "Needs my review" })).toBeChecked();
  await expect(pending).toBeVisible();
  await expect(rows.filter({ hasText: "Realization Rate (monthly, by region)" })).toHaveCount(0);
  await screenshot(page, "02-library-needs-review");
  await page.getByRole("checkbox", { name: "Needs my review" }).click();
  await expect(page.getByRole("checkbox", { name: "Needs my review" })).not.toBeChecked();

  // Created by agent: the agent's composed definition and its pending proposal only.
  const agent = page.getByRole("group", { name: "Created by" }).getByRole("checkbox", { name: "Agent" });
  await agent.click();
  await expect(agent).toBeChecked();
  await expect(rows).toHaveCount(2);
  await agent.click();
  await expect(agent).not.toBeChecked();

  // /review (the old queue) lands on the Library with Needs my review on.
  await page.goto("/review");
  await expect(page).toHaveURL(/\/\?review=mine$/);
  await expect(page.getByRole("checkbox", { name: "Needs my review" })).toBeChecked();

  // Rejected rows are hidden by default and shown with the Rejected filter.
  const rejected = await api("POST", `/proposals/${proposalId}/reject`, tokens.admin, { comment: "E2E cleanup" });
  expect(rejected.status).toBe(200);
  await page.goto("/");
  await expect(rows).toHaveCount(4);
  await page.getByRole("group", { name: "Approval" }).getByRole("checkbox", { name: "Rejected" }).click();
  await expect(rows.filter({ hasText: "E2E pending combo" }).getByText("rejected", { exact: true })).toBeVisible();
  await screenshot(page, "02-library-rejected");
});
