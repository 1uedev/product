import { defineConfig } from "@playwright/test";

// E2E tests run against a real compose stack (scripts/test_e2e.sh): real Keycloak login, real API, worker, storage.
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:18480";

export default defineConfig({
  testDir: "./specs",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  use: {
    baseURL,
    locale: "de-DE",
    timezoneId: "Europe/Berlin",
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ["--no-sandbox"] } : { args: ["--no-sandbox"] },
  },
});
