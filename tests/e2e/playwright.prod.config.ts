import base from "./playwright.config";

// Optional production-mode smoke test (see docs/VERIFICATION.md). E2E_PROD_MAP_HOST=1 maps the public host name to 127.0.0.1
// (useful for CADDY_TLS_MODE=internal trials without DNS). The browser must not use the environment proxy for that.
const origin = process.env.E2E_PROD_ORIGIN ?? "https://localhost";
const host = new URL(origin).hostname;
const args = ["--no-sandbox", "--no-proxy-server", ...(process.env.E2E_PROD_MAP_HOST ? [`--host-resolver-rules=MAP ${host} 127.0.0.1`] : [])];

export default {
  ...base,
  testMatch: /99-prod-smoke\.spec\.ts/,
  use: { ...base.use, launchOptions: { executablePath: process.env.CHROMIUM_PATH, args } },
};
