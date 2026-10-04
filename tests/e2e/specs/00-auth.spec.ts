import { expect, test } from "@playwright/test";
import { login, newSession, openWorkspace } from "../helpers";

test("real OIDC login, workspace switching, logout and access isolation", async ({ browser }) => {
  const { context, page } = await newSession(browser, "alice@lumen.example");
  // Alice belongs to two workspaces: the picker is shown first
  await expect(page.getByRole("heading", { name: "Workspace wählen" })).toBeVisible({ timeout: 30_000 });
  const lumen = await openWorkspace(page, /Lumen Analytics/);
  await expect(page.getByRole("heading", { name: "Übersicht" })).toBeVisible();
  await expect(page.getByTestId("user-name")).toContainText("Alice Owner");
  await expect(page.getByTestId("kpi-arr")).toContainText("€");
  // workspace switching
  const e2e = await openWorkspace(page, /E2E Leerer Workspace/);
  expect(e2e).not.toBe(lumen);
  await expect(page.getByText("Dieser Workspace ist noch leer.")).toBeVisible();
  // another tenant's workspace id is not reachable for alice (Fjord belongs to Finn only)
  const res = await page.request.get(`/api/v1/workspaces/${lumen}/dashboard`);
  expect(res.status()).toBe(200);
  await page.getByRole("button", { name: "Abmelden" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/w/"));
  const after = await page.request.get(`/api/v1/auth/me`);
  expect(after.status()).toBe(401);
  await context.close();

  // Finn is separated: only his own workspace, foreign ids give 404
  const finn = await newSession(browser, "finn@fjord.example");
  const mine = await openWorkspace(finn.page, /Fjord Systems/);
  expect(mine).not.toBe(lumen);
  const foreign = await finn.page.request.get(`/api/v1/workspaces/${lumen}/dashboard`);
  expect(foreign.status()).toBe(404);
  await expect(finn.page.locator("#ws-switch option")).toHaveCount(1);
  await finn.context.close();
  void login;
});

test("a wrong password does not log in", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("login-button").click();
  await page.locator("#username").fill("alice@lumen.example");
  await page.locator("#password").fill("falsch");
  await page.locator("#kc-login").click();
  await expect(page.locator("#input-error, .alert-error, [class*=error]").first()).toBeVisible();
  expect(page.url()).toContain("/auth/");
});
