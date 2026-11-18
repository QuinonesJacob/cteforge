import { api, expect, screenshot, signIn, test, tokens } from "./helpers";

interface Card {
  version: number;
  keywords: string[];
}
interface Outcome {
  proposal: { id: string; status: string };
  auto_applied: boolean;
}

/** A metadata-only revision (one more keyword) proposed directly through the API. */
async function revise(id: string, token: string, keyword: string, summary: string): Promise<string> {
  const card = (await api<Card>("GET", `/definitions/${id}`, tokens.admin)).json;
  const res = await api<Outcome>("POST", `/proposals/definitions/${id}/revise`, token, {
    base_version: card.version,
    metadata: { keywords: [...card.keywords, keyword] },
    summary,
  });
  expect(res.status).toBe(200);
  expect(res.json.auto_applied).toBe(false); // strict mode: it waits for a person
  expect(res.json.proposal.status).toBe("pending");
  return res.json.proposal.id;
}

/** An agent's pending change to the demo's composed definition: parts pinned to their
 * current versions and the final SELECT ordered (strict mode, so it waits). */
async function proposeQueryChange(): Promise<string> {
  interface Composed {
    version: number;
    composition: { parts: { alias: string; id: string; version?: number | null }[]; select: string; fixed: Record<string, Record<string, unknown>> };
  }
  const card = (await api<Composed>("GET", "/definitions/realization_with_booked", tokens.admin)).json;
  const uses = Object.fromEntries(card.composition.parts.map((p) => [p.alias, { id: p.id, version: p.version }]));
  const res = await api<Outcome>("POST", "/proposals/definitions/realization_with_booked/revise", tokens.agent, {
    base_version: card.version,
    composition: { uses, select: `${card.composition.select.trim().replace(/;$/, "")}\nORDER BY 1`, fixed: card.composition.fixed },
    summary: "Pin the parts and order the rows",
  });
  expect(res.status).toBe(200);
  expect(res.json.proposal.status).toBe("pending");
  return res.json.proposal.id;
}

test.describe.configure({ mode: "serial" });

test.afterAll(async () => {
  // Leave the shared server in open mode for any later spec.
  await api("PUT", "/settings", tokens.admin, { mode: "open" });
});

test("admin switches the team to strict mode in Settings", async ({ page }) => {
  await signIn(page, tokens.admin, "/settings/team");
  const strict = page.getByRole("radio", { name: /Strict/ });
  await expect(page.getByRole("radio", { name: /Open/ })).toBeChecked();
  await strict.click(); // controlled: checked once the server has stored it
  await expect(page.getByRole("status").filter({ hasText: "Team mode set to strict" })).toBeVisible();
  await expect(strict).toBeChecked();
  await screenshot(page, "05-settings");
  // The setting is stored, not just shown.
  await page.reload();
  await expect(page.getByRole("radio", { name: /Strict/ })).toBeChecked();
});

test("an agent's revision waits in the Library; a second human approves and verifies it", async ({ page }) => {
  const summary = "Add cash conversion keyword";
  const id = await revise("realization_rate", tokens.agent, "cash conversion", summary);

  // The admin sees it in the Library under "Needs my review": a badge on the definition's
  // row links to the proposal (ADR 0026: no separate Review page).
  await signIn(page, tokens.admin, "/?review=mine");
  const row = page.locator("article.hit").filter({ hasText: "Realization Rate (monthly, by region)" });
  await expect(row.getByText("needs my review")).toBeVisible();
  const badge = row.getByRole("link", { name: "Revision pending" });
  await expect(badge).toHaveAttribute("href", `/review/${id}`);
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: /^Library/ }).getByLabel(/pending/)).toBeVisible();
  await screenshot(page, "05-library-needs-review");
  await badge.click();
  // ADR 0029: kind + status, the definition's title, the proposer and their note.
  await expect(page.getByRole("heading", { level: 1, name: "Realization Rate (monthly, by region)" })).toBeVisible();
  await expect(page.locator(".prop-kind")).toContainText("Change to a definition");
  await expect(page.locator(".status-chip")).toHaveText("Waiting on you");
  await expect(page.locator(".lead")).toContainText(`Proposed by claude-agent (agent)`);
  await expect(page.locator(".lead")).toContainText(`“${summary}”`);

  // ADR 0029: a keyword is a description change: the query does not change, and the
  // change shows as a field, not as stored YAML (no version / state lines).
  await expect(page.getByText(/The query does not change/)).toBeVisible();
  const described = page.getByRole("region", { name: "Description changes" });
  await expect(described).toContainText("Keywords");
  await expect(described).toContainText("cash conversion");
  await expect(page.getByText(/^version:/)).toHaveCount(0);
  // Activity: real events only.
  await expect(page.getByRole("region", { name: "Activity" })).toContainText("claude-agent (agent) proposed this");

  // Ben (approver, human, not the author) approves with "also verify".
  await page.getByRole("button", { name: /^Your account/ }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, tokens.approver, `/review/${id}`);
  await expect(page.getByRole("heading", { level: 1, name: "Realization Rate (monthly, by region)" })).toBeVisible();
  await expect(page.getByText(/cash conversion/).first()).toBeVisible();
  const decide = page.getByRole("region", { name: "Decide" });
  await expect(decide.getByRole("heading", { name: "Your decision" })).toBeVisible();
  await decide.getByRole("checkbox", { name: /Also verify the definition/ }).check();
  await decide.getByLabel(/Note to claude-agent/).fill("Looks right.");
  await screenshot(page, "05-proposal-detail");
  await decide.getByRole("button", { name: "Approve and verify" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Proposal approved" })).toBeVisible();
  await expect(page.locator(".status-chip")).toHaveText("Approved");
  const activity = page.getByRole("region", { name: "Activity" });
  await expect(activity).toContainText("ben approved it");
  await expect(activity).toContainText("Looks right.");

  // The definition is verified, with verifier and date (small text, no banner).
  await activity.getByRole("link", { name: "realization_rate", exact: true }).click();
  await expect(page).toHaveURL(/\/definitions\/realization_rate$/);
  const state = page.locator(".def-state");
  await expect(state.getByRole("img", { name: "Verified" })).toBeVisible();
  const today = new Date();
  await expect(state).toContainText(/Verified by ben on \S+/);
  await expect(state).toContainText(String(today.getFullYear()));
  // Already verified: no Verify button.
  await expect(page.getByRole("button", { name: /^Verify v/ })).toHaveCount(0);
  await screenshot(page, "05-definition-verified");

  // Strict mode: the verified definition runs (an agent, through the API) labeled verified.
  const run = await api<{ label: string; ran: boolean }>("POST", "/run", tokens.agent, {
    uses: { rr: { id: "realization_rate" } },
    values: {},
    run: true,
    limit: 5,
  });
  expect(run.status).toBe(200);
  expect(run.json.label).toBe("verified");
});

test("a query change: the trusted SQL diff, unified or side by side, changes only or full", async ({ page }) => {
  // An agent pins the composed definition's parts and orders its final SELECT (strict mode:
  // it waits). The diff is of the query — parts list and SELECT — not the stored YAML.
  const id = await proposeQueryChange();
  try {
    await signIn(page, tokens.admin, `/review/${id}`);
    const diff = page.locator("figure.diff");
    await expect(diff.locator(".sql-label")).toHaveText(/^realization_with_booked\.sql · v\d+ → proposed$/);
    await expect(diff.getByLabel(/lines added/)).toHaveText(/^\+[1-9]\d*$/);
    const unified = diff.getByRole("region", { name: "Unified diff" });
    await expect(unified).toContainText(/-- part \w+: \w+ v\d+/);
    await expect(unified).toContainText("ORDER BY");
    await expect(unified).not.toContainText("schema_version");
    await expect(unified).not.toContainText("state:");
    await diff.getByRole("button", { name: "Full" }).click();
    await expect(diff.getByRole("button", { name: "Full" })).toHaveAttribute("aria-pressed", "true");
    await expect(diff.getByText(/unchanged lines?$/)).toHaveCount(0);
    await diff.getByRole("button", { name: "Side by side" }).click();
    await expect(diff.getByRole("region", { name: "Side-by-side diff" })).toContainText("ORDER BY");
    await screenshot(page, "05-proposal-diff-split");
    await diff.getByRole("button", { name: "Changes only" }).click();
    await diff.getByRole("button", { name: "Unified" }).click();
    await screenshot(page, "05-proposal-query-diff");
    // The Library's waiting panel counts the same query lines.
    await page.goto("/");
    const item = page.locator(".waiting-item").filter({ hasText: "Realization rate next to booked revenue" });
    await expect(item.locator(".add")).toHaveText(/^\+[1-9]\d*$/);
  } finally {
    await api("POST", `/proposals/${id}/withdraw`, tokens.agent);
  }
});

test("four-eyes: the author of a proposal is not offered approval", async ({ page }) => {
  const summary = "Add booked revenue keyword";
  const id = await revise("booked_vs_collected", tokens.approver, "booked revenue", summary);

  await signIn(page, tokens.approver, `/review/${id}`);
  await expect(page.getByText(`“${summary}”`)).toBeVisible();
  const decide = page.getByRole("region", { name: "Decide" });
  await expect(decide).toContainText("You wrote this proposal, so another approver must approve it");
  await expect(decide.getByRole("button", { name: /^Approve/ })).toHaveCount(0);
  await expect(decide.getByRole("checkbox", { name: /Also verify/ })).toHaveCount(0);
  await expect(decide.getByRole("button", { name: "Withdraw" })).toBeEnabled();
  await screenshot(page, "05-four-eyes");
  // The server enforces it too.
  const forced = await api<{ error: { type: string; message: string } }>("POST", `/proposals/${id}/approve`, tokens.approver, {
    verify: false,
    comment: "",
  });
  expect(forced.status).toBe(403);
  expect(forced.json.error.message).toContain("four-eyes");

  await decide.getByRole("button", { name: "Withdraw" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Proposal withdrawn" })).toBeVisible();
  await expect(page.locator(".status-chip")).toHaveText("Withdrawn");
});
