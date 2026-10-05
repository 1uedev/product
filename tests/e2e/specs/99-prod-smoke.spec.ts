import { expect, test } from "@playwright/test";

// Optional: login + invitation in PRODUCTION mode (HTTPS issuer, __Host- cookie, no demo data). Needs a running prod stack:
// E2E_PROD_ORIGIN=https://host:port E2E_PROD_USER=... E2E_PROD_PASSWORD=... E2E_PROD_INVITE_TOKEN=... (see docs/VERIFICATION.md)
const origin = process.env.E2E_PROD_ORIGIN;
test.skip(!origin, "production smoke test not configured");
test.use({ ignoreHTTPSErrors: true, baseURL: origin });

test("production mode: HTTPS login, invitation accepted, secure cookie, no demo content", async ({ browser }) => {
  const host = new URL(origin!).hostname;
  const port = new URL(origin!).port || "443";
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  await page.goto(`/invite?token=${process.env.E2E_PROD_INVITE_TOKEN}`);
  await expect(page.getByRole("heading", { name: "Einladung" })).toBeVisible();
  await expect(page.getByText("Demo-Modus")).toHaveCount(0);
  await page.getByRole("link", { name: "Anmelden und Einladung annehmen" }).click();
  await page.locator("#username").fill(process.env.E2E_PROD_USER!);
  await page.locator("#password").fill(process.env.E2E_PROD_PASSWORD!);
  await page.locator("#kc-login").click();
  await page.waitForURL(/\/invite\?token=/);
  await page.getByRole("button", { name: "Einladung annehmen" }).click();
  await page.waitForURL(/\/w\/[0-9a-f-]{36}/);
  await expect(page.getByRole("heading", { name: "Übersicht" })).toBeVisible();
  await expect(page.getByText("Dieser Workspace ist noch leer.")).toBeVisible();
  await expect(page.getByText("Demo-Modus")).toHaveCount(0);
  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name.endsWith("de_session"))!;
  expect(session.name.startsWith("__Host-")).toBe(true);
  expect(session.secure).toBe(true);
  expect(session.httpOnly).toBe(true);
  expect(session.sameSite).toBe("Lax");
  void host; void port;
  await context.close();
});
