import { expect, test } from "@playwright/test";
import { newSession, openWorkspace } from "../helpers";

test.describe.configure({ mode: "serial" });

test("viewer: read-only in the UI and refused by the server", async ({ browser }) => {
  const { page, context } = await newSession(browser, "vera@lumen.example");
  const ws = await openWorkspace(page, /Lumen Analytics/);
  await page.goto(`/w/${ws}/problems`);
  await expect(page.getByTestId("problem-table")).toBeVisible();
  await expect(page.getByTestId("start-analysis")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Neues Problem" })).toHaveCount(0);
  await page.goto(`/w/${ws}/import`);
  await expect(page.getByText("Ihre Rolle darf Importe nur einsehen")).toBeVisible();
  await expect(page.getByRole("link", { name: "Audit" })).toHaveCount(0);
  await page.goto(`/w/${ws}/audit`);
  await expect(page.getByText("sichtbar").first()).toBeVisible();
  // the server enforces it independent of the UI
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  const h = { "X-CSRF-Token": me.csrf_token };
  expect((await page.request.post(`/api/v1/workspaces/${ws}/problems`, { data: { title: "x" }, headers: h })).status()).toBe(403);
  expect((await page.request.post(`/api/v1/workspaces/${ws}/analysis`, { data: { scope: "all" }, headers: h })).status()).toBe(403);
  expect((await page.request.get(`/api/v1/workspaces/${ws}/audit`)).status()).toBe(403);
  // CSRF: a state-changing request without the token is rejected even for an authorised role
  const ed = await newSession(browser, "bob@lumen.example");
  const edWs = await openWorkspace(ed.page, /Lumen Analytics/);
  const noToken = await ed.page.request.post(`/api/v1/workspaces/${edWs}/problems`, { data: { title: "ohne Token" } });
  expect(noToken.status()).toBe(403);
  const foreignOrigin = await ed.page.request.post(`/api/v1/workspaces/${edWs}/problems`, {
    data: { title: "falscher Ursprung" }, headers: { "X-CSRF-Token": (await (await ed.page.request.get("/api/v1/auth/me")).json()).csrf_token, Origin: "http://evil.example" },
  });
  expect(foreignOrigin.status()).toBe(403);
  await ed.context.close();
  await context.close();
});

test("editor cannot approve; admin sees audit and settings", async ({ browser }) => {
  const ed = await newSession(browser, "bob@lumen.example");
  const ws = await openWorkspace(ed.page, /Lumen Analytics/);
  await ed.page.goto(`/w/${ws}/decisions`);
  await ed.page.getByTestId("decision-table").getByRole("link").first().click();
  await expect(ed.page.getByTestId("decision-approve")).toHaveCount(0);
  await ed.context.close();
  const ad = await newSession(browser, "dora@lumen.example");
  const adWs = await openWorkspace(ad.page, /Lumen Analytics/);
  await ad.page.goto(`/w/${adWs}/audit`);
  await expect(ad.page.getByTestId("audit-table")).toBeVisible();
  await ad.page.goto(`/w/${adWs}/settings`);
  await expect(ad.page.getByRole("heading", { name: "Grenzen" })).toBeVisible();
  await ad.context.close();
});

test("seeded demo data: approved decision stays historical while new knowledge is shown separately", async ({ browser }) => {
  const { page, context } = await newSession(browser, "dora@lumen.example");
  const ws = await openWorkspace(page, /Lumen Analytics/);
  await page.goto(`/w/${ws}/decisions`);
  const approved = page.getByTestId("decision-table").locator("tbody tr", { hasText: "Freigegeben" }).first();
  await approved.getByRole("link").first().click();
  await expect(page.getByTestId("decision-state")).toContainText("Freigegeben");
  await expect(page.getByTestId("drift-notice")).toContainText("Aktueller Wissensstand weicht vom Belegstand ab");
  await expect(page.getByTestId("new-revision")).toBeVisible();
  await expect(page.getByTestId("decision-save")).toHaveCount(0);
  await context.close();
});

test("invitations: single use, bound to the e-mail address, role is respected; last owner is protected", async ({ browser }) => {
  const owner = await newSession(browser, "alice@lumen.example");
  const ws = await openWorkspace(owner.page, /E2E Leerer Workspace/);
  await owner.page.goto(`/w/${ws}/members`);
  // last-owner protection: Alice is the only owner of this workspace
  const roleSelect = owner.page.getByLabel("Rolle von Alice Owner");
  await roleSelect.selectOption("admin");
  await expect(owner.page.locator(".toast.error")).toContainText(/Owner/);
  await owner.page.reload();
  await expect(owner.page.getByLabel("Rolle von Alice Owner")).toHaveValue("owner");

  // invite Finn (exists in the identity provider, belongs to another tenant only) as viewer
  await owner.page.getByTestId("invite-open").click();
  await owner.page.getByTestId("invite-email").fill("Finn@Fjord.example");
  await owner.page.getByTestId("invite-role").selectOption("viewer");
  await owner.page.getByTestId("invite-create").click();
  const link = await owner.page.getByTestId("invite-link").inputValue();
  expect(link).toContain("/invite?token=");
  // a second invitation for somebody else to test the e-mail binding
  await owner.page.getByRole("button", { name: "Schließen" }).click();
  await owner.page.getByTestId("invite-open").click();
  await owner.page.getByTestId("invite-email").fill("vera@lumen.example");
  await owner.page.getByTestId("invite-role").selectOption("editor");
  await owner.page.getByTestId("invite-create").click();
  const veraLink = await owner.page.getByTestId("invite-link").inputValue();
  await owner.context.close();

  const finn = await newSession(browser, "finn@fjord.example");
  // wrong person using Vera's link is refused (the token alone is not enough)
  await finn.page.goto(new URL(veraLink).pathname + new URL(veraLink).search);
  await finn.page.getByRole("button", { name: "Einladung annehmen" }).click();
  await expect(finn.page.locator(".notice.danger")).toContainText("E-Mail-Adresse als Ihr Konto");
  // the invited person accepts and lands in the workspace with the invited role
  await finn.page.goto(new URL(link).pathname + new URL(link).search);
  await finn.page.getByRole("button", { name: "Einladung annehmen" }).click();
  await finn.page.waitForURL(new RegExp(`/w/${ws}`));
  await expect(finn.page.locator("header .badge.info")).toHaveText("Viewer");
  // single use
  await finn.page.goto(new URL(link).pathname + new URL(link).search);
  await expect(finn.page.locator(".notice.danger")).toContainText(/verwendet|abgelaufen|zurückgezogen/);
  await finn.context.close();
});
