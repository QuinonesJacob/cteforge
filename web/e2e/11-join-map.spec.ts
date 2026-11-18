import { api, expect, screenshot, signIn, test, tokens, type Page } from "./helpers";

// ADR 0026, 0029: the join map is mined from definitions; people annotate edges through
// proposals (the GUI adds none); an edge an agent adds waits for a person and is not known
// until then.
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

/** Opens a row's detail (the first cell's button) and returns it. */
async function openRow(page: Page, edgeId: string) {
  const row = page.locator(`tr[data-edge="${edgeId}"]`);
  const toggle = row.locator("button.row-toggle");
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  return { row, detail: page.locator(`[id="join-detail-${edgeId}"]`) };
}

test("the list view: sides, keys, join-type variants, used by, trust; a row shows its sources and where they join", async ({
  page,
}) => {
  await signIn(page, tokens.admin, "/joins");
  await expect(page.getByRole("heading", { level: 1, name: "Join map" })).toBeVisible();
  const table = page.getByRole("region", { name: "Join map" });
  await expect(table.getByRole("columnheader")).toHaveText([/Many side/, /One side/, /Keys/, /Type/, /Used by/, /Trust/, "Flags"]);
  const { row, detail } = await openRow(page, MINED);
  await expect(row.locator(".jt-chip").first()).toHaveText(/^(INNER|LEFT|RIGHT|FULL)( \d+)?$/);
  await expect(row.getByRole("img", { name: /verified|LLM-annotated|draft/ })).toBeVisible();
  await expect(row.getByText("Known", { exact: true })).toBeVisible();
  // Each source definition with its join type and location (CTE / main query / subquery).
  await expect(detail.getByRole("link", { name: "Realization Rate (monthly, by region)" })).toBeVisible();
  await expect(detail).toContainText(/(INNER|LEFT|RIGHT|FULL) in (main query|CTE \w+|subquery)/);
  await screenshot(page, "11-join-map");
  // Searching fades the rows that don't match; sorting by a column.
  await page.getByLabel("Search joins").fill("zzz-no-such-table");
  await expect(row).toHaveClass(/row-faded/);
  await page.getByLabel("Search joins").fill("");
  await table.getByRole("button", { name: /^Used by/ }).click();
  await expect(table.getByRole("columnheader", { name: /Used by/ })).toHaveAttribute("aria-sort", "ascending");
  // Nobody adds joins from the GUI (ADR 0029): agents may propose_join add through MCP.
  await expect(page.getByRole("button", { name: "Add a join" })).toHaveCount(0);
  // No "known only" filter (ADR 0029): the Known / Confirmed flags say it per row.
  await expect(page.getByRole("checkbox", { name: /Known/ })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: "Variants first" })).toBeVisible();
});

test("a person annotates a mined edge: the relationship is checked against the warehouse", async ({ page }) => {
  await signIn(page, tokens.admin, "/joins");
  await expect(page.getByRole("heading", { level: 1, name: "Join map" })).toBeVisible();
  const { row, detail } = await openRow(page, MINED);
  await expect(detail).toContainText("realization_rate"); // mined from the demo definitions
  await expect(row.getByText("Known", { exact: true })).toBeVisible();

  await detail.getByRole("button", { name: `Annotate ${MINED}` }).click();
  const form = page.getByRole("form", { name: `Annotate ${MINED}` });
  await form.getByLabel("Relationship").selectOption("one_to_many");
  await form.getByLabel("Traps").fill("fan-out: a deal can have several invoices");
  await form.getByLabel("Summary").fill("E2E: deals have many invoices");
  await screenshot(page, "11-join-annotate");
  await form.getByRole("button", { name: "Propose annotation" }).click();
  // Open mode, a person, checks pass (deals.deal_id is unique, invoices.deal_id is not).
  await expect(page.getByRole("status").filter({ hasText: "Join annotated" })).toBeVisible();
  // one to many: invoices is the many side now, listed first.
  await expect(row.locator("td").first()).toContainText("invoices");
  await expect(detail).toContainText("one to many");
  await expect(detail).toContainText("fan-out: a deal can have several invoices");
  await expect(detail.getByLabel("Relationship evidence")).toContainText("consistent");
  await screenshot(page, "11-join-detail");

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
  const proposed = page.locator("tr.row-faded", { has: page.getByRole("link", { name: "Proposed" }) });
  await expect(proposed).toContainText("by claude-agent (agent)");
  await expect(proposed).toContainText("status = status");
  await expect(page.locator(`tr[data-edge="${edgeId}"]`)).toHaveCount(0); // not in the map yet
  await proposed.getByRole("link", { name: "Proposed" }).click();
  await expect(page).toHaveURL(new RegExp(`/review/${res.json.proposal.id}$`));
  const card = page.getByRole("region", { name: "Proposed join", exact: true });
  await expect(card).toContainText("Added by an agent");
  const decide = page.getByRole("region", { name: "Decide" });
  await expect(decide.getByRole("checkbox", { name: /Also verify/ })).toHaveCount(0); // joins are not verified
  await screenshot(page, "11-join-proposal");
  await decide.getByRole("button", { name: /^Approve/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Proposal approved" })).toBeVisible();

  await page.goto("/joins");
  const { row, detail } = await openRow(page, edgeId);
  await expect(detail).toContainText("added by claude-agent (agent)");
  await expect(row.getByText("Known", { exact: true })).toBeVisible();
  await expect(row.getByText("Confirmed", { exact: true })).toBeVisible();
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
  const { detail } = await openRow(page, MINED);
  await expect(detail).toBeVisible();
  await expect(page.getByRole("button", { name: "Add a join" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Annotate/ })).toHaveCount(0);
});
