// ADR 0029: every redesigned page, light and dark, for comparing with the design boards
// (e2e/screenshots/12-<page>-<theme>.png). Runs last, on the state the other specs left.
import { api, expect, screenshot, signIn, test, tokens, type Page } from "./helpers";

interface Composed {
  version: number;
  keywords: string[];
  composition: { parts: { alias: string; id: string; version?: number | null }[]; select: string; fixed: Record<string, Record<string, unknown>> };
}

test.describe.configure({ mode: "serial" });

let proposalId = "";

test.beforeAll(async () => {
  // A pending change (strict mode: it waits for a person) for the proposal page: the
  // composed definition's parts pinned and its final SELECT ordered (a query change), and
  // its usage notes reworded (a described-field change).
  await api("PUT", "/settings", tokens.admin, { mode: "strict" });
  const card = (await api<Composed>("GET", "/definitions/realization_with_booked", tokens.admin)).json;
  const uses = Object.fromEntries(card.composition.parts.map((p) => [p.alias, { id: p.id, version: p.version }]));
  const res = await api<{ proposal: { id: string } }>("POST", "/proposals/definitions/realization_with_booked/revise", tokens.agent, {
    base_version: card.version,
    composition: { uses, select: `${card.composition.select.trim().replace(/;$/, "")}\nORDER BY 1`, fixed: card.composition.fixed },
    metadata: { keywords: [...card.keywords, "realized revenue"], use_when: "Monthly realization next to bookings.\nComparing regions." },
    summary: "Pin the parts, order the rows and split the usage notes",
  });
  proposalId = res.json.proposal.id;
  await api("PUT", "/settings", tokens.admin, { mode: "open" });
});

test.afterAll(async () => {
  if (proposalId) await api("POST", `/proposals/${proposalId}/withdraw`, tokens.agent);
});

const PAGES: [string, string, (page: Page) => Promise<void>][] = [
  ["library", "/", async (page) => void (await expect(page.locator("article.hit").first()).toBeVisible())],
  ["definition", "/definitions/realization_rate", async (page) => void (await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible())],
  ["import", "/import", async (page) => void (await expect(page.getByLabel(/Trusted SQL/)).toBeVisible())],
  ["proposal", "", async (page) => void (await expect(page.locator("figure.diff")).toBeVisible())],
  ["join-map", "/joins", async (page) => void (await expect(page.locator("tr[data-edge]").first()).toBeVisible())],
  ["log", "/log", async (page) => void (await expect(page.getByRole("region", { name: "Log events" })).toBeVisible())],
  ["settings", "/settings/users", async (page) => void (await expect(page.getByRole("region", { name: "Users" })).toBeVisible())],
];

for (const theme of ["light", "dark"] as const) {
  test(`pages in ${theme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await signIn(page, tokens.admin);
    for (const [name, path, ready] of PAGES) {
      await page.goto(path || `/review/${proposalId}`);
      await ready(page);
      await page.waitForLoadState("networkidle");
      await screenshot(page, `12-${name}-${theme}`);
    }
    // The import flow's variables step and the user menu.
    await page.goto("/import");
    await page.getByLabel(/Trusted SQL/).fill(
      "SELECT status, COUNT(*) AS n\nFROM invoices\nWHERE invoiced_at >= DATE '2026-01-01'\n  AND invoiced_at < DATE '2026-04-01'\n  AND status <> 'void'\nGROUP BY status",
    );
    await page.getByRole("button", { name: "Parse", exact: true }).click();
    await expect(page.locator(".marked-sql")).toBeVisible();
    await screenshot(page, `12-import-values-${theme}`);
    // A join's detail.
    await page.goto("/joins");
    await page.locator("tr[data-edge] button.row-toggle").first().click();
    await screenshot(page, `12-join-detail-${theme}`);
    // A Log row's detail.
    await page.goto("/log");
    await page.getByRole("region", { name: "Log events" }).locator("button.row-toggle").first().click();
    await screenshot(page, `12-log-detail-${theme}`);
    await page.goto("/");
    await page.getByRole("button", { name: /^Your account/ }).click();
    await expect(page.getByRole("group", { name: "Account" })).toBeVisible();
    await screenshot(page, `12-user-menu-${theme}`);
  });
}
