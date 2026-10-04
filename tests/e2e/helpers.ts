import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const DEMO_PASSWORD = "demo-Passw0rd!";
export const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
export const fixture = (name: string) => join(FIXTURES, name);
export const fixtureText = (name: string) => readFileSync(fixture(name), "utf-8");

/** Real OIDC login through Keycloak (no mocking). */
export async function login(page: Page, email: string, next = "/"): Promise<void> {
  await page.goto(next);
  const button = page.getByTestId("login-button");
  const username = page.locator("#username");
  await button.or(username).first().waitFor({ state: "visible", timeout: 30_000 });
  if (await button.isVisible()) await button.click();
  await page.locator("#username").fill(email);
  await page.locator("#password").fill(DEMO_PASSWORD);
  await page.locator("#kc-login").click();
  await page.waitForURL((url) => !url.pathname.startsWith("/auth/") && !url.pathname.startsWith("/api/"), { timeout: 60_000 });
}

export async function newSession(browser: Browser, email: string, next = "/"): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  await login(page, email, next);
  return { context, page };
}

/** Opens the workspace whose name matches and returns its id. Works from the picker page and from inside a workspace. */
export async function openWorkspace(page: Page, name: RegExp): Promise<string> {
  if (!/\/w\/[0-9a-f-]{36}/.test(page.url())) await page.goto("/");
  const switcher = page.locator("#ws-switch");
  const picker = page.getByRole("heading", { name: "Workspace wählen" });
  await switcher.or(picker).first().waitFor({ state: "visible", timeout: 30_000 });
  if (await picker.isVisible()) {
    await page.getByRole("link", { name }).click();
    await expect(switcher).toBeVisible();
  }
  const current = await page.locator("#ws-switch option:checked").textContent();
  if (!name.test(current ?? "")) {
    const value = await page.locator("#ws-switch option", { hasText: name }).first().getAttribute("value");
    await switcher.selectOption(value!);
  }
  await expect(page.locator("#ws-switch option:checked")).toHaveText(name);
  await page.waitForURL(/\/w\/[0-9a-f-]{36}/);
  await expect(page.locator("main h1").first()).toBeVisible();
  return page.url().match(/\/w\/([0-9a-f-]{36})/)![1]!;
}
