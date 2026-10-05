import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Takes the screenshots of the user manual (docs/benutzerhandbuch) from a running demo/test stack:
//   E2E_BASE_URL=http://localhost:8481 pnpm exec playwright test -c playwright.screenshots.config.ts
// It works through the real UI with real logins. Run it against a fresh stack of scripts/test_e2e.sh (KEEP_TEST_PROJECT=1).
export default defineConfig({
  ...base,
  testDir: "./screenshots",
  testMatch: /capture\.spec\.ts/,
  reporter: [["list"]],
  use: { ...base.use, viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, trace: "off", screenshot: "off" },
});
