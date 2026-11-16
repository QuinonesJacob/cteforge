// Real-browser end-to-end tests of the GUI (web/CLAUDE.md "e2e"). Uses the installed
// Google Chrome (channel "chrome"); Playwright's own browsers are never downloaded.
// globalSetup builds the GUI, runs `cteforge demo` and starts `cteforge serve`.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts$/,
  globalSetup: "./e2e/global-setup.ts",
  // The specs share one server and change its state in order (open → strict mode).
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    channel: "chrome",
    headless: true,
    viewport: { width: 1280, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
