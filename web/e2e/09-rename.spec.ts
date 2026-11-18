import { api, expect, screenshot, signIn, test, tokens } from "./helpers";

// ADR 0024, 0026. The demo's composed definition realization_with_booked uses
// booked_vs_collected (llm_annotated) as part "bc", and the team is in open mode, so the
// rename applies at once.
test.describe.configure({ mode: "serial" });

const OLD = "booked_vs_collected";
const NEW = "booked_vs_collected_by_region";

interface ErrorBody {
  error: { type: string; message: string };
}

test("rename a definition used by a composed definition: references, typed confirmation, applied", async ({ page }) => {
  await signIn(page, tokens.admin, `/definitions/${OLD}?tab=edit`);
  const panel = page.getByRole("region", { name: "Rename ID" });
  await expect(panel.getByRole("heading", { name: "Rename ID" })).toBeVisible();
  // Everything inside cteforge that uses the ID, grouped by kind, and the outside warning.
  await expect(panel.getByText(/^\d+ composed definitions?$/)).toBeVisible();
  await expect(panel.getByRole("link", { name: "realization_with_booked" })).toBeVisible();
  await expect(panel.getByRole("note")).toContainText("References outside cteforge will break");

  const rename = panel.getByRole("button", { name: /^Rename/ });
  await expect(rename).toBeDisabled();
  await panel.getByLabel(/^New ID/).fill(NEW);
  await panel.getByLabel(/^Type the new ID to confirm/).fill(`${NEW}x`);
  await expect(rename).toBeDisabled();
  await panel.getByLabel(/^Type the new ID to confirm/).fill(NEW);
  await screenshot(page, "09-rename-confirm");
  await expect(rename).toBeEnabled();
  await rename.click();

  // Open mode, not verified: applied at once; the page moves to the new ID.
  await expect(page.getByRole("status").filter({ hasText: `Renamed ${OLD} to ${NEW}` })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/definitions/${NEW}$`));
  await expect(page.locator(".def-state")).toContainText(`formerly ${OLD}`);
  await screenshot(page, "09-renamed-definition");

  // The old ID fails with an error naming the new one (what an agent with stale
  // instructions sees), and the composed definition now uses (and runs with) the new ID.
  const old = await api<ErrorBody>("GET", `/definitions/${OLD}`, tokens.agent);
  expect(old.status).toBe(404);
  expect(old.json.error.message).toContain(`was renamed to '${NEW}'`);
  const run = await api<{ ran: boolean; definitions: string[] }>("POST", "/run", tokens.agent, {
    uses: { combo: { id: "realization_with_booked" } },
    values: {},
    run: true,
    limit: 5,
  });
  expect(run.status).toBe(200);
  expect(run.json.ran).toBe(true);
  expect(run.json.definitions.some((d) => d.startsWith(`${NEW}@`))).toBe(true);
  // Searching the old name still finds it.
  const hits = await api<{ id: string }[]>("GET", `/definitions?q=${OLD}`, tokens.agent);
  expect(hits.json.map((h) => h.id)).toContain(NEW);
});

test("a rename proposal shows old → new and the references on its proposal page", async ({ page }) => {
  // An agent proposes the rename back while the team is strict, so it waits for a person.
  await api("PUT", "/settings", tokens.admin, { mode: "strict" });
  try {
    const res = await api<{ proposal: { id: string; status: string } }>(
      "POST",
      `/proposals/definitions/${NEW}/rename`,
      tokens.agent,
      { new_id: "booked_collected", summary: "Shorter name" },
    );
    expect(res.status).toBe(200);
    expect(res.json.proposal.status).toBe("pending");
    await signIn(page, tokens.approver, `/review/${res.json.proposal.id}`);
    const card = page.getByRole("region", { name: "Proposed rename" });
    await expect(card).toContainText(`${NEW} → booked_collected`);
    await expect(card.getByRole("link", { name: "realization_with_booked" })).toBeVisible();
    const decide = page.getByRole("region", { name: "Decide" });
    await expect(decide.getByRole("checkbox", { name: /Also verify/ })).toHaveCount(0);
    await screenshot(page, "09-rename-proposal");
    await decide.getByLabel(/Note to/).fill("Keep the descriptive name");
    await decide.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Proposal rejected" })).toBeVisible();
  } finally {
    await api("PUT", "/settings", tokens.admin, { mode: "open" });
  }
});
