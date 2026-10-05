import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { newSession, openWorkspace } from "../helpers";

// Automated WCAG 2.x A/AA checks (axe) on the main pages plus keyboard operation. Automated checks find only part of the issues.
async function expectNoViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${label}: ${v.id} (${v.nodes.length}x) ${v.nodes[0]?.target.join(" ")}`), `${label}: axe violations`).toEqual([]);
}

test("main pages have no serious accessibility violations and the UI works with the keyboard", async ({ browser }) => {
  const { page, context } = await newSession(browser, "dora@lumen.example");
  const ws = await openWorkspace(page, /Lumen Analytics/);
  const pages: [string, string][] = [
    ["overview", ""], ["import", "/import"], ["sources", "/sources"], ["customers", "/customers"], ["problems", "/problems"],
    ["initiatives", "/initiatives"], ["compare", "/compare"], ["decisions", "/decisions"], ["jobs", "/jobs"], ["members", "/members"], ["audit", "/audit"], ["settings", "/settings"],
  ];
  for (const [label, path] of pages) {
    await page.goto(`/w/${ws}${path}`);
    await expect(page.locator("main h1").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expectNoViolations(page, label);
  }
  // detail pages
  await page.goto(`/w/${ws}/problems`);
  await page.getByTestId("problem-table").getByRole("link").first().click();
  await expect(page.getByTestId("problem-title")).toBeVisible();
  await page.getByTestId("open-evidence").first().click();
  await page.waitForLoadState("networkidle");
  await expectNoViolations(page, "problem detail with evidence panel");
  await page.goto(`/w/${ws}/decisions`);
  await page.getByTestId("decision-table").getByRole("link").first().click();
  await expect(page.getByTestId("decision-heading")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expectNoViolations(page, "decision");
  await context.close();

  // login page
  const anon = await browser.newContext();
  const login = await anon.newPage();
  await login.goto("/");
  await expect(login.getByTestId("login-button")).toBeVisible();
  await expectNoViolations(login, "login");
  await anon.close();
});

test("keyboard: skip link, visible focus, dialogs open and close with the keyboard", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example");
  const ws = await openWorkspace(page, /Lumen Analytics/);
  await page.goto(`/w/${ws}/problems`);
  await expect(page.locator("main h1").first()).toBeVisible();      // the shell (with the skip link) is rendered after the session loaded
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Zum Inhalt springen" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  // focus indicator is drawn (outline width from :focus-visible)
  const outline = await skip.evaluate((el) => getComputedStyle(el).outlineStyle);
  expect(outline).not.toBe("none");
  // open the "new problem" dialog with the keyboard, focus moves inside, Escape closes it
  await page.getByRole("button", { name: "Neues Problem" }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  // tab order reaches the problem table links
  await page.getByLabel("Titel enthält").focus();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toBeVisible();
  await context.close();
});

test("responsive: no horizontal page scroll on a phone viewport", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const { login } = await import("../helpers");
  await login(page, "bob@lumen.example");
  const ws = await openWorkspace(page, /Lumen Analytics/);
  for (const path of ["", "/problems", "/decisions", "/import"]) {
    await page.goto(`/w/${ws}${path}`);
    await expect(page.locator("main h1").first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow on ${path || "overview"}`).toBeLessThanOrEqual(1);
  }
  await context.close();
});
