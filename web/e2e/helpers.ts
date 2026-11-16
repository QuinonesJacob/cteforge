// Shared fixtures for the e2e specs: the server URL and tokens from global-setup, sign-in,
// direct API calls (for steps a person would do elsewhere, e.g. an agent's proposal) and
// screenshots for the orchestrator.
import { test as base, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SHOTS = join(dirname(fileURLToPath(import.meta.url)), "screenshots");

function env(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} not set: run through \`npm run e2e\` (global-setup)`);
  return value;
}

export const tokens = {
  get admin() {
    return env("E2E_ADMIN_TOKEN");
  },
  get agent() {
    return env("E2E_AGENT_TOKEN");
  },
  /** "ben": a second human approver (not admin), created by global-setup. */
  get approver() {
    return env("E2E_APPROVER_TOKEN");
  },
};

export const test = base.extend({
  baseURL: async ({}, use) => {
    await use(env("E2E_BASE_URL"));
  },
});
export { expect };
export type { Page };

/** Call the HTTP API directly with a token (JSON in, JSON out). */
export async function api<T = unknown>(method: string, path: string, token: string, body?: unknown): Promise<{ status: number; json: T }> {
  const res = await fetch(`${env("E2E_BASE_URL")}/api/v1${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: (text ? JSON.parse(text) : null) as T };
}

/** Sign in through the sign-in form, like a person pasting their token. */
export async function signIn(page: Page, token: string, path = "/"): Promise<void> {
  await page.goto(path);
  await page.getByLabel("API token").fill(token);
  await page.getByRole("button", { name: "Sign in" }).click();
  // The top bar (the nav itself collapses behind a menu button on narrow screens).
  await expect(page.getByRole("banner")).toBeVisible();
}

export async function screenshot(page: Page, name: string): Promise<void> {
  mkdirSync(SHOTS, { recursive: true });
  // From the top, so the sticky top bar is not stamped mid-page in a full-page capture.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
}

/** True when the page scrolls sideways (content wider than the viewport). */
export async function hasHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
}
