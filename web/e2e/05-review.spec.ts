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

test.describe.configure({ mode: "serial" });

test.afterAll(async () => {
  // Leave the shared server in open mode for any later spec.
  await api("PUT", "/settings", tokens.admin, { mode: "open" });
});

test("admin switches the team to strict mode in Settings", async ({ page }) => {
  await signIn(page, tokens.admin, "/settings");
  const strict = page.getByRole("radio", { name: /Strict/ });
  await expect(page.getByRole("radio", { name: /Open/ })).toBeChecked();
  await strict.click(); // controlled: checked once the server has stored it
  await expect(page.getByRole("status").filter({ hasText: "Team mode set to strict" })).toBeVisible();
  await expect(strict).toBeChecked();
  await expect(page.getByRole("cell", { name: "ben", exact: true })).toBeVisible();
  await screenshot(page, "05-settings");
  // The setting is stored, not just shown.
  await page.reload();
  await expect(page.getByRole("radio", { name: /Strict/ })).toBeChecked();
});

test("an agent's revision waits in Review; a second human approves and verifies it", async ({ page }) => {
  const summary = "Add cash conversion keyword";
  const id = await revise("realization_rate", tokens.agent, "cash conversion", summary);

  // The admin sees it pending in the Review queue.
  await signIn(page, tokens.admin, "/review");
  await expect(page.getByRole("tab", { name: /Pending/ })).toHaveAttribute("aria-selected", "true");
  const item = page.locator(".proposal-item").filter({ hasText: summary });
  await expect(item).toBeVisible();
  await expect(item).toContainText("claude-agent");
  await expect(item).toContainText("pending");
  await expect(page.getByRole("link", { name: /Review/ }).getByLabel(/pending/)).toBeVisible();
  await screenshot(page, "05-review-queue");

  // Ben (approver, human, not the author) approves with "also verify".
  await page.getByRole("button", { name: /admin/ }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, tokens.approver, `/review/${id}`);
  await expect(page.getByRole("heading", { level: 1, name: summary })).toBeVisible();
  await expect(page.getByText(/cash conversion/).first()).toBeVisible();
  const decide = page.getByRole("region", { name: "Decide" });
  await decide.getByRole("checkbox", { name: /Also verify the definition/ }).check();
  await decide.getByLabel("Comment").fill("Looks right.");
  await screenshot(page, "05-proposal-detail");
  await decide.getByRole("button", { name: "Approve and verify" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Proposal approved" })).toBeVisible();
  await expect(page.locator(".def-meta").getByText("approved", { exact: true })).toBeVisible();
  await expect(page.getByText(/Decided by ben/)).toBeVisible();

  // The definition is verified, with verifier and date.
  await page.getByRole("link", { name: "realization_rate", exact: true }).click();
  await expect(page).toHaveURL(/\/definitions\/realization_rate$/);
  const meta = page.locator(".def-meta");
  await expect(meta.getByText("Verified", { exact: true })).toBeVisible();
  const today = new Date();
  await expect(meta).toContainText(/verified by ben on \S+/);
  await expect(meta).toContainText(String(today.getFullYear()));
  // Already verified: no Verify button.
  await expect(page.getByRole("button", { name: /^Verify v/ })).toHaveCount(0);
  await screenshot(page, "05-definition-verified");

  // Strict mode: the verified definition runs and its result is labeled verified.
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByRole("button", { name: "Run", exact: true }).click();
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.locator(".badge").first()).toContainText("Verified");
  await expect(result.getByRole("list", { name: "Definitions used" })).toContainText(/verified by ben on/);
});

test("four-eyes: the author of a proposal is not offered approval", async ({ page }) => {
  const summary = "Add booked revenue keyword";
  const id = await revise("booked_vs_collected", tokens.approver, "booked revenue", summary);

  await signIn(page, tokens.approver, `/review/${id}`);
  await expect(page.getByRole("heading", { level: 1, name: summary })).toBeVisible();
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
  await expect(page.locator(".def-meta").getByText("withdrawn", { exact: true })).toBeVisible();
});
