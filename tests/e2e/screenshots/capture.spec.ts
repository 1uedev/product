import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { DEMO_PASSWORD, fixture, newSession, openWorkspace } from "../helpers";

// Screenshots for docs/benutzerhandbuch. One continuous story, real logins, real data (see playwright.screenshots.config.ts).
test.describe.configure({ mode: "serial" });

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "docs", "benutzerhandbuch", "bilder");
mkdirSync(OUT, { recursive: true });

let e2eWs = "";
let lumenWs = "";
let decisionPath = "";
const e2e = () => `/w/${e2eWs}`;
const lumen = () => `/w/${lumenWs}`;

/** Resolves the workspace ids by name, so that single tests can be re-run on their own. */
async function resolveIds(page: Page) {
  if (lumenWs && e2eWs) return;
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  for (const w of me.workspaces as { tenant_id: string; name: string }[]) {
    if (/Lumen Analytics/.test(w.name)) lumenWs = w.tenant_id;
    if (/E2E Leerer Workspace/.test(w.name)) e2eWs = w.tenant_id;
  }
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.locator(".toast").first().waitFor({ state: "hidden", timeout: 10_000 }).catch(() => undefined);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
}

/** Screenshot of the visible window (default), the whole page, or one element. Optional highlight outlines an element. */
async function shot(page: Page, name: string, opts: { full?: boolean; of?: Locator; mark?: Locator[] } = {}) {
  await settle(page);
  const marks = opts.mark ?? [];
  if (!opts.of && !opts.full) {
    if (marks.length) await marks[0]!.scrollIntoViewIfNeeded();
    else await page.evaluate(() => window.scrollTo(0, 0));
  }
  for (const m of marks) await m.evaluate((el) => { (el as HTMLElement).style.outline = "3px solid #d9480f"; (el as HTMLElement).style.outlineOffset = "2px"; });
  const path = join(OUT, `${name}.png`);
  if (opts.of) await opts.of.screenshot({ path });
  else await page.screenshot({ path, fullPage: opts.full ?? false });
  for (const m of marks) await m.evaluate((el) => { (el as HTMLElement).style.outline = ""; (el as HTMLElement).style.outlineOffset = ""; });
}

async function importCsv(page: Page, kind: string, file: string) {
  await page.goto(`${e2e()}/import`);
  await page.getByTestId("import-kind").selectOption(kind);
  await page.getByTestId("import-file").setInputFiles(fixture(file));
  await page.getByTestId("import-upload").click();
  await expect(page.getByTestId("import-preview")).toBeVisible();
  await page.getByTestId("import-preview").click();
  await expect(page.getByTestId("import-preview-card")).toBeVisible();
}

async function commitImport(page: Page) {
  await expect(page.getByTestId("import-commit")).toBeEnabled();
  await page.getByTestId("import-commit").click();
  await expect(page.getByTestId("import-result")).toBeVisible({ timeout: 90_000 });
}

test("01 Anmeldung und Workspace-Auswahl", async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/");
  await expect(page.getByTestId("login-button")).toBeVisible();
  await shot(page, "01-startseite");
  await page.getByTestId("login-button").click();
  await page.locator("#username").waitFor();
  await shot(page, "02-anmeldung-keycloak");
  await page.locator("#username").fill("alice@lumen.example");
  await page.locator("#password").fill(DEMO_PASSWORD);
  await page.locator("#kc-login").click();
  await page.waitForURL((u) => !u.pathname.startsWith("/auth/") && !u.pathname.startsWith("/api/"), { timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "Workspace wählen" })).toBeVisible();
  await shot(page, "03-workspace-waehlen");
  lumenWs = await openWorkspace(page, /Lumen Analytics/);
  e2eWs = (await page.locator("#ws-switch option", { hasText: /E2E Leerer Workspace/ }).first().getAttribute("value")) ?? "";
  expect(e2eWs).not.toBe("");
  await expect(page.getByTestId("kpi-arr")).toBeVisible();
  await shot(page, "04-uebersicht", { full: true });
  await shot(page, "05-workspace-wechseln", { of: page.locator("header").first() });
  await context.close();
});

test("02 Import mit Vorschau", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example");
  await resolveIds(page);
  await page.goto(e2e());
  await expect(page.getByText("Dieser Workspace ist noch leer.")).toBeVisible();
  await shot(page, "10-uebersicht-leer");
  await page.goto(`${e2e()}/import`);
  await shot(page, "11-import-start", { full: true });

  // rejected upload
  await page.getByTestId("import-kind").selectOption("customers");
  await page.getByTestId("import-file").setInputFiles(fixture("getarnt.csv"));
  await page.getByTestId("import-upload").click();
  await expect(page.locator(".notice.danger").first()).toBeVisible();
  await shot(page, "12-import-abgelehnt");

  // erroneous file: row errors, commit disabled
  await importCsv(page, "customers", "kunden-fehlerhaft.csv");
  await expect(page.getByTestId("import-commit")).toBeDisabled();
  await shot(page, "13-import-fehlerhafte-vorschau", { full: true });

  // valid customers: mapping + preview
  await importCsv(page, "customers", "kunden.csv");
  await shot(page, "14-import-zuordnung-und-vorschau", { full: true });
  await commitImport(page);
  await shot(page, "15-import-ergebnis");

  await importCsv(page, "opportunities", "opportunities.csv");
  await commitImport(page);
  await importCsv(page, "feedback", "feedback.csv");
  await expect(page.getByTestId("import-preview-card")).toContainText("2 Dubletten");
  await shot(page, "16-import-dubletten", { full: true });
  await commitImport(page);

  // text note
  await page.goto(`${e2e()}/import`);
  await page.getByRole("tab", { name: "Gesprächsnotiz einfügen" }).click();
  await page.getByLabel("Titel").fill("Jour fixe Notiz");
  await page.getByLabel("Text", { exact: true }).fill(readFileSync(fixture("gespraechsnotiz.txt"), "utf-8"));
  await page.getByLabel("Kunden-ID (optional)").fill("K-1001");
  await shot(page, "17-import-gespraechsnotiz", { full: true });
  await page.getByRole("button", { name: "Prüfen" }).click();
  await commitImport(page);

  await page.goto(e2e());
  await expect(page.getByTestId("kpi-arr")).toContainText("CHF");
  await shot(page, "18-uebersicht-nach-import", { full: true });
  await context.close();
});

test("03 Quellen, Kunden und Suche", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example");
  await resolveIds(page);
  await page.goto(`${lumen()}/sources`);
  await expect(page.getByTestId("feedback-q")).toBeVisible();
  await page.getByTestId("feedback-q").fill("Export");
  await expect(page.getByTestId("feedback-list")).toBeVisible();
  await shot(page, "20-quellen-suche");
  await page.goto(`${lumen()}/customers`);
  await expect(page.getByTestId("customer-table")).toBeVisible();
  await shot(page, "21-kunden", { full: true });
  await context.close();
});

test("04 Analyse, Probleme und Belege", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example");
  await resolveIds(page);
  await page.goto(`${e2e()}/problems`);
  await expect(page.getByText("Noch keine Probleme")).toBeVisible();
  await shot(page, "30-probleme-leer");
  await page.getByTestId("start-analysis").click();
  await expect(page.getByTestId("job-progress")).toBeVisible();
  await shot(page, "31-analyse-laeuft");
  await expect(page.getByTestId("job-progress")).toHaveAttribute("data-status", "succeeded", { timeout: 120_000 });
  await expect(page.getByTestId("problem-table").locator("tbody tr").first()).toBeVisible();
  await shot(page, "32-probleme-liste", { full: true });

  await page.getByTestId("problem-table").getByRole("link", { name: /export/i }).first().click();
  await expect(page.getByTestId("problem-title")).toContainText(/export/i);
  await shot(page, "33-problem-detail", { full: true });
  await page.getByRole("tab", { name: /Kennzahlen/ }).click();
  await expect(page.getByTestId("metric-customers")).toBeVisible();
  await shot(page, "34-problem-kennzahlen", { full: true });
  await page.getByRole("tab", { name: /Belege/ }).click();
  await page.getByTestId("open-evidence").first().click();
  await expect(page.getByTestId("evidence-text").locator("mark")).toBeVisible();
  await shot(page, "35-beleg-seitenpanel", { mark: [page.getByTestId("evidence-panel")] });

  // correct the relation of a piece of evidence
  await page.getByTestId("open-evidence").nth(1).click();
  await page.getByTestId("evidence-relation").selectOption("context");
  await expect(page.getByText("Beleg aktualisiert.")).toBeVisible();

  // split
  const boxes = page.getByRole("checkbox", { name: "Beleg auswählen" });
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await page.getByTestId("split-open").click();
  await page.getByTestId("split-title").fill("Abgespaltener Export-Teil");
  await shot(page, "36-problem-abspalten");
  await page.getByTestId("split-confirm").click();
  await expect(page.getByTestId("problem-title")).toHaveText("Abgespaltener Export-Teil");

  // merge back through the list
  await page.goto(`${e2e()}/problems`);
  const table = page.getByTestId("problem-table");
  await table.locator("tbody tr").filter({ hasText: /export|csv/i }).filter({ hasNotText: "Abgespaltener" }).first().getByRole("checkbox").check();
  await page.getByRole("checkbox", { name: "Abgespaltener Export-Teil auswählen" }).check();
  await page.getByTestId("merge-open").click();
  await shot(page, "37-probleme-zusammenfuehren");
  await page.getByTestId("merge-confirm").click();
  await expect(page.getByTestId("problem-title")).not.toHaveText("Abgespaltener Export-Teil");
  await page.getByRole("tab", { name: "Verlauf" }).click();
  await expect(page.locator("strong", { hasText: "Probleme zusammengeführt" }).first()).toBeVisible();
  await shot(page, "38-problem-verlauf", { full: true });

  // rename + confirm
  await page.getByRole("button", { name: "Bearbeiten" }).click();
  await page.getByTestId("edit-title").fill("CSV-Export bricht bei großen Tabellen ab");
  await shot(page, "39-problem-bearbeiten");
  await page.getByTestId("edit-save").click();
  await page.getByTestId("problem-confirm").click();
  await expect(page.getByText("Bestätigt").first()).toBeVisible();
  await shot(page, "40-problem-bestaetigt");
  await context.close();
});

test("05 Initiative und Entscheidungsvorlage", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example");
  await resolveIds(page);
  await page.goto(`${e2e()}/problems`);
  await page.getByTestId("problem-table").getByRole("link", { name: /CSV-Export/ }).first().click();
  await page.getByTestId("new-initiative").click();
  await page.getByTestId("initiative-title").fill("Streaming-Export");
  await page.getByLabel("Gewünschtes Ergebnis").fill("Große Tabellen lassen sich zuverlässig exportieren.");
  await page.getByLabel("Aufwand von").fill("20");
  await page.getByLabel("Aufwand bis").fill("35");
  await shot(page, "50-initiative-anlegen");
  await page.getByTestId("initiative-create").click();
  await expect(page.getByTestId("initiative-heading")).toHaveText("Streaming-Export");

  await page.getByTestId("add-assumption").click();
  await page.getByTestId("assumption-statement").fill("Abbrüche entstehen ab etwa 50.000 Zeilen.");
  await page.getByTestId("assumption-kind").selectOption("observed");
  await page.getByLabel("Quelle (Beleg des Problems)").selectOption({ index: 1 });
  await shot(page, "51-annahme-erfassen");
  await page.getByTestId("assumption-save").click();
  await page.getByTestId("add-assumption").click();
  await page.getByTestId("assumption-statement").fill("Umsetzung in 20 bis 35 Personentagen.");
  await page.getByTestId("assumption-kind").selectOption("estimate");
  await page.getByTestId("assumption-save").click();
  await expect(page.getByTestId("initiative-score")).toBeVisible();
  await shot(page, "52-initiative-detail", { full: true });

  await page.getByTestId("create-decision").click();
  await expect(page.getByTestId("decision-state")).toContainText("Entwurf");
  await page.getByTestId("decision-title").fill("Entscheidungsvorlage: Streaming-Export");
  await page.getByTestId("option-name-0").fill("Streaming-Export");
  await page.getByTestId("option-low-0").fill("20");
  await page.locator("#o0-h").fill("35");
  await page.locator("#o0-rr").selectOption("medium");
  await page.getByTestId("option-name-1").fill("Limit mit Hinweis");
  await page.getByTestId("option-low-1").fill("3");
  await page.locator("#o1-h").fill("6");
  await page.locator("#o1-s").fill("smb");
  await page.locator("#o1-rr").selectOption("low");
  await page.getByTestId("decision-text").fill("Wir empfehlen den Streaming-Export, weil mehrere Kunden unabhängig voneinander Abbrüche melden und die Belege aktuell sind.");
  await shot(page, "53-entscheidung-optionen", { of: page.locator('section[aria-labelledby="opts"]') });
  await shot(page, "53b-entscheidung-begruendung", { of: page.locator('section[aria-labelledby="rec"]') });
  await page.getByTestId("decision-save").click();
  await expect(page.getByText("Entwurf gespeichert.")).toBeVisible();
  await page.getByTestId("snapshot-refresh").click();
  await expect(page.getByTestId("scoring-card")).toBeVisible();
  await shot(page, "54-entscheidung-belegstand-scoring", { of: page.getByTestId("scoring-card") });
  await page.getByTestId("ai-draft-request").click();
  await expect(page.getByTestId("ai-draft")).toBeVisible({ timeout: 90_000 });
  await shot(page, "55-entscheidung-ki-entwurf", { of: page.getByTestId("ai-draft") });
  await page.getByTestId("decision-submit").click();
  await expect(page.getByTestId("decision-state")).toContainText("In Prüfung");
  await page.getByTestId("comment-body").fill("Bitte die Aufwandsschätzung prüfen.");
  await page.getByTestId("comment-add").click();
  await expect(page.getByTestId("comments")).toContainText("Bitte die Aufwandsschätzung prüfen.");
  await shot(page, "56-entscheidung-in-pruefung");
  await shot(page, "57-pruefung-und-freigabe", { of: page.locator('section[aria-labelledby="wf"]') });
  await shot(page, "58-kommentare", { of: page.locator('section[aria-labelledby="com"]') });
  decisionPath = page.url().replace(/^https?:\/\/[^/]+/, "");
  await context.close();
});

test("06 Freigabe und Export", async ({ browser }) => {
  const { page, context } = await newSession(browser, "dora@lumen.example", decisionPath);
  await resolveIds(page);
  await expect(page.getByTestId("decision-approve")).toBeEnabled();
  await shot(page, "60-freigabe-bereit", { mark: [page.getByTestId("decision-approve")] });
  await page.getByTestId("decision-approve").click();
  await expect(page.getByTestId("decision-state")).toContainText("Freigegeben");
  await shot(page, "61-freigegeben");
  await shot(page, "61b-revisionen", { of: page.locator('section[aria-labelledby="rev"]') });
  await shot(page, "62-export-schaltflaechen", { mark: [page.getByTestId("export-md"), page.getByTestId("export-csv")] });
  await page.goto(`${e2e()}/audit`);
  await expect(page.getByTestId("audit-table")).toContainText("decision.approved");
  await shot(page, "63-audit-protokoll", { full: true });
  await context.close();
});

test("07 Vergleich, Initiativen, Entscheidungen, Aufgaben", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example");
  await resolveIds(page);
  await page.goto(`${lumen()}/initiatives`);
  await expect(page.getByTestId("initiative-table")).toBeVisible();
  await shot(page, "70-initiativen-liste");
  await page.goto(`${lumen()}/compare`);
  const picks = page.locator('section[aria-labelledby="sel"] input[type=checkbox]');
  await expect(picks.first()).toBeVisible();
  for (let i = 0; i < await picks.count(); i++) await picks.nth(i).check();
  await page.getByTestId("compare-run").click();
  await expect(page.getByTestId("compare-results")).toBeVisible();
  await shot(page, "71-vergleich");
  await shot(page, "71b-vergleich-ergebnis", { of: page.getByTestId("compare-results") });
  await page.goto(`${lumen()}/decisions`);
  await expect(page.getByTestId("decision-table")).toBeVisible();
  await shot(page, "72-entscheidungen-liste");
  await page.getByTestId("decision-table").locator("tbody tr", { hasText: "Freigegeben" }).first().getByRole("link").first().click();
  await expect(page.getByTestId("decision-state")).toContainText("Freigegeben");
  await expect(page.getByTestId("drift-notice")).toBeVisible();
  await shot(page, "73-entscheidung-historisch");
  await shot(page, "73b-aenderung-seit-freigabe", { of: page.getByTestId("drift-notice") });
  await shot(page, "73c-neue-revision", { mark: [page.getByTestId("new-revision")] });
  await page.goto(`${e2e()}/jobs`);
  await expect(page.getByTestId("job-table")).toBeVisible();
  await shot(page, "74-aufgaben");
  await context.close();
});

test("08 Mitglieder, Einstellungen und Rollen", async ({ browser }) => {
  const ad = await newSession(browser, "dora@lumen.example");
  await resolveIds(ad.page);
  await ad.page.goto(`${lumen()}/members`);
  await expect(ad.page.getByTestId("member-table")).toBeVisible();
  // earlier runs of this script may have left open invitations behind
  for (let n = await ad.page.getByRole("button", { name: "Zurückziehen" }).count(); n > 0; n--) {
    await ad.page.getByRole("button", { name: "Zurückziehen" }).first().click();
    await expect(ad.page.getByRole("button", { name: "Zurückziehen" })).toHaveCount(n - 1);
  }
  await shot(ad.page, "80-mitglieder", { full: true });
  await ad.page.getByTestId("invite-open").click();
  await ad.page.getByTestId("invite-email").fill("neu@lumen.example");
  await ad.page.getByTestId("invite-role").selectOption("editor");
  await shot(ad.page, "81-einladung-anlegen");
  await ad.page.getByTestId("invite-create").click();
  await expect(ad.page.getByTestId("invite-link")).toBeVisible();
  // the one-time token must not end up in the manual
  await ad.page.getByTestId("invite-link").evaluate((el) => { (el as HTMLInputElement).value = "https://decisions.example.org/invite?token=EINMALIGER-TOKEN"; });
  await shot(ad.page, "82-einladung-erzeugt");
  await ad.page.goto(`${lumen()}/settings`);
  await expect(ad.page.getByRole("heading", { name: "Grenzen" })).toBeVisible();
  await shot(ad.page, "83-einstellungen", { full: true });
  await ad.context.close();

  const viewer = await newSession(browser, "vera@lumen.example");
  await resolveIds(viewer.page);
  await viewer.page.goto(`${lumen()}/import`);
  await expect(viewer.page.getByText(/Importe sind für Editoren/)).toBeVisible();
  await shot(viewer.page, "84-berechtigung-viewer");
  await viewer.context.close();
});

test("09 Mobile Ansicht und zweiter Mandant", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true });
  const page = await context.newPage();
  const { login } = await import("../helpers");
  await login(page, "bob@lumen.example");
  await resolveIds(page);
  await page.goto(`${lumen()}/problems`);
  await expect(page.getByTestId("problem-table")).toBeVisible();
  await shot(page, "90-mobil-probleme");
  await context.close();
});
