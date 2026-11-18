import { api, expect, screenshot, signIn, test, tokens, type Page } from "./helpers";

interface Outcome {
  proposal: { id: string; status: string };
  auto_applied: boolean;
}

/** Opens the Filters popover (ADR 0029) unless it is open. */
async function filters(page: Page) {
  const button = page.getByRole("button", { name: /^Filters/ });
  if ((await button.getAttribute("aria-expanded")) !== "true") await button.click();
  return page.getByRole("group", { name: "Filters" });
}

test("the library lists the demo definitions (one composed by the agent), filters by words, opens one", async ({ page }) => {
  await signIn(page, tokens.admin);
  const rows = page.locator("article.hit");
  await expect(rows).toHaveCount(4);
  await expect(page.getByText("4 definitions", { exact: true })).toBeVisible();
  await expect(page.getByText("Sorted by relevance, then trust")).toBeVisible();
  const composed = rows.filter({ hasText: "Realization rate next to booked revenue" });
  await expect(composed.getByText("composed", { exact: true })).toBeVisible();
  await expect(composed).toContainText("Created by claude-agent (agent)");
  // Trust icons and their legend.
  await expect(rows.first().getByRole("img").first()).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Trust and review" })).toContainText("LLM-annotated");
  await screenshot(page, "02-library");

  const search = page.getByRole("searchbox", { name: "Search the library" });
  await search.fill("grain");
  await expect(page).toHaveURL(/[?&]q=grain/);
  await expect(rows).toHaveCount(1);
  await expect(rows.first().locator(".lib-row-title")).toHaveText(/by period grain/);
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

  // Needs my review: only what this person may approve. Filters are a popover; chosen ones
  // show as removable chips. They are controlled by the URL: click, then wait.
  const review = (await filters(page)).getByRole("checkbox", { name: "Needs my review" });
  await review.click();
  await expect(page).toHaveURL(/[?&]review=mine/);
  await expect(review).toBeChecked();
  await expect(pending).toBeVisible();
  await expect(rows.filter({ hasText: "Realization Rate (monthly, by region)" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Remove filter Review: Needs my review" })).toBeVisible();
  await screenshot(page, "02-library-needs-review");
  await review.click();
  await expect(review).not.toBeChecked();

  // Created by agent: the agent's composed definition and its pending proposal only.
  const agent = (await filters(page)).getByRole("group", { name: "Created by" }).getByRole("checkbox", { name: "Agent" });
  await agent.click();
  await expect(agent).toBeChecked();
  await expect(rows).toHaveCount(2);
  await page.getByRole("button", { name: "Clear all" }).last().click();
  await expect(page).toHaveURL(/\/$/);
  await expect(rows).toHaveCount(5);

  // /review (the old queue) lands on the Library with Needs my review on.
  await page.goto("/review");
  await expect(page).toHaveURL(/\/\?review=mine$/);
  await expect((await filters(page)).getByRole("checkbox", { name: "Needs my review" })).toBeChecked();

  // Rejected rows are hidden by default and shown with the Rejected filter.
  const rejected = await api("POST", `/proposals/${proposalId}/reject`, tokens.admin, { comment: "E2E cleanup" });
  expect(rejected.status).toBe(200);
  await page.goto("/");
  await expect(rows).toHaveCount(4);
  await (await filters(page)).getByRole("group", { name: "Approval" }).getByRole("checkbox", { name: "Rejected" }).click();
  await expect(rows.filter({ hasText: "E2E pending combo" }).getByText("rejected", { exact: true })).toBeVisible();
  await screenshot(page, "02-library-rejected");
});

test("waiting on you: proposals the person may decide, oldest first, with +/− lines; the queue sorts oldest first", async ({
  page,
}) => {
  const propose = async (id: string, title: string) => {
    const res = await api<Outcome>("POST", "/proposals/definitions", tokens.agent, {
      composition: { uses: { x: { id: "no_such_definition" } } },
      metadata: { id, title },
      summary: `E2E: ${title}`,
    });
    expect(res.json.proposal.status).toBe("pending");
    return res.json.proposal.id;
  };
  const older = await propose("e2e_waiting_older", "E2E waiting older");
  const newer = await propose("e2e_waiting_newer", "E2E waiting newer");
  try {
    await signIn(page, tokens.admin);
    const panel = page.getByRole("region", { name: /proposals?$/ });
    await expect(panel.getByText("Waiting on you")).toBeVisible();
    const items = panel.getByRole("listitem");
    await expect(items.filter({ hasText: "E2E waiting older" })).toContainText(/\+\d+/);
    await expect(items.filter({ hasText: "E2E waiting older" })).toContainText("by claude-agent (agent)");
    const titles = await panel.locator(".waiting-name").allTextContents();
    expect(titles.indexOf("E2E waiting older")).toBeLessThan(titles.indexOf("E2E waiting newer"));
    await screenshot(page, "02-library-waiting");

    await panel.getByRole("link", { name: "Open review queue →" }).click();
    await expect(page).toHaveURL(/\/\?review=mine&sort=oldest$/);
    await expect(page.getByText("Sorted by the oldest proposal waiting")).toBeVisible();
    // The list reloads after the URL changes: wait for the sorted rows.
    await expect(async () => {
      const queue = await page.locator("article.hit .lib-row-title").allTextContents();
      expect(queue.indexOf("E2E waiting older")).toBeGreaterThanOrEqual(0);
      expect(queue.indexOf("E2E waiting older")).toBeLessThan(queue.indexOf("E2E waiting newer"));
    }).toPass();
    // Removing the sort chip goes back to relevance.
    await page.getByRole("button", { name: "Remove filter Sort: Oldest first" }).click();
    await expect(page).toHaveURL(/\/\?review=mine$/);
  } finally {
    for (const id of [older, newer]) await api("POST", `/proposals/${id}/withdraw`, tokens.agent);
  }
});
