import { api, expect, screenshot, signIn, test, tokens } from "./helpers";

// ADR 0026: the join map is mined from definitions; people annotate edges and add new ones
// through proposals; an edge an agent adds waits for a person and is not known until then.
test.describe.configure({ mode: "serial" });

const MINED = "deals__invoices__deal_id=deal_id";

interface Outcome {
  proposal: { id: string; status: string; target_id: string };
  auto_applied: boolean;
}
interface Edge {
  id: string;
  known: boolean;
  confirmed: boolean;
  confirmed_by?: string | null;
  relationship?: string | null;
  evidence?: { consistent: boolean } | null;
}

test("a person annotates a mined edge: the relationship is checked against the warehouse", async ({ page }) => {
  await signIn(page, tokens.admin, "/joins");
  await expect(page.getByRole("heading", { level: 1, name: "Join map" })).toBeVisible();
  const row = page.locator(`tr[data-edge="${MINED}"]`);
  await expect(row).toContainText("realization_rate"); // mined from the demo definitions
  await expect(row.getByText("known", { exact: true })).toBeVisible();
  await screenshot(page, "11-join-map");

  await row.getByRole("button", { name: `Annotate ${MINED}` }).click();
  const form = page.getByRole("form", { name: `Annotate ${MINED}` });
  await form.getByLabel("Relationship").selectOption("one_to_many");
  await form.getByLabel("Traps").fill("fan-out: a deal can have several invoices");
  await form.getByLabel("Summary").fill("E2E: deals have many invoices");
  await screenshot(page, "11-join-annotate");
  await form.getByRole("button", { name: "Propose annotation" }).click();
  // Open mode, a person, checks pass (deals.deal_id is unique, invoices.deal_id is not).
  await expect(page.getByRole("status").filter({ hasText: "Join annotated" })).toBeVisible();
  await expect(row).toContainText("one to many");
  await expect(row).toContainText("fan-out: a deal can have several invoices");
  await row.getByText("evidence").click();
  await expect(row.getByLabel("Relationship evidence")).toContainText("consistent");

  const edges = await api<Edge[]>("GET", "/joins?table=invoices", tokens.admin);
  const edge = edges.json.find((e) => e.id === MINED);
  expect(edge?.relationship).toBe("one_to_many");
  expect(edge?.evidence?.consistent).toBe(true);
});

test("an edge an agent adds waits for a person; approved, it becomes known and confirmed", async ({ page }) => {
  const res = await api<Outcome>("POST", "/proposals/joins", tokens.agent, {
    action: "add",
    left_table: "deals",
    right_table: "invoices",
    keys: [{ left: "status", right: "status" }],
    summary: "E2E: agent adds a join",
  });
  expect(res.status).toBe(200);
  expect(res.json.auto_applied).toBe(false); // never auto-applied, even in open mode
  expect(res.json.proposal.status).toBe("pending");
  const edgeId = res.json.proposal.target_id;

  await signIn(page, tokens.admin, "/joins");
  const proposed = page.getByRole("region", { name: "Proposed joins" });
  await expect(proposed).toContainText("by claude-agent (agent)");
  await expect(page.locator(`tr[data-edge="${edgeId}"]`)).toHaveCount(0); // not in the map yet
  await proposed.getByRole("link", { name: "deals ↔ invoices" }).click();
  await expect(page).toHaveURL(new RegExp(`/review/${res.json.proposal.id}$`));
  const card = page.getByRole("region", { name: "Proposed join", exact: true });
  await expect(card).toContainText("Added by an agent");
  const decide = page.getByRole("region", { name: "Decide" });
  await expect(decide.getByRole("checkbox", { name: /Also verify/ })).toHaveCount(0); // joins are not verified
  await screenshot(page, "11-join-proposal");
  await decide.getByRole("button", { name: /^Approve/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Proposal approved" })).toBeVisible();

  await page.goto("/joins");
  const row = page.locator(`tr[data-edge="${edgeId}"]`);
  await expect(row).toContainText("added by claude-agent (agent)");
  await expect(row.getByText("known", { exact: true })).toBeVisible();
  await expect(row.getByText("confirmed", { exact: true })).toBeVisible();
  const edges = await api<Edge[]>("GET", "/joins", tokens.admin);
  expect(edges.json.find((e) => e.id === edgeId)).toMatchObject({ known: true, confirmed: true, confirmed_by: "admin" });
});

test("a viewer-like token sees the map but no forms", async ({ page }) => {
  // The agent's contributor token may propose joins; the approver token too. A token with
  // list_joins but not propose_join sees no forms: use the approver with a narrow token.
  const minted = await api<{ token: string }>("POST", "/me/tokens", tokens.approver, {
    name: "e2e joins reader",
    tool_mode: "only",
    include: ["search", "list_joins"],
    exclude: [],
  });
  expect(minted.status).toBe(201);
  await signIn(page, minted.json.token, "/joins");
  await expect(page.locator(`tr[data-edge="${MINED}"]`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Add a join" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Annotate/ })).toHaveCount(0);
});
