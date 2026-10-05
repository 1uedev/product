import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fixture, newSession, openWorkspace } from "../helpers";

// One continuous story in the empty E2E workspace, with real OIDC logins (editor Bob, admin Dora).
test.describe.configure({ mode: "serial" });

let workspaceId = "";
const base = () => `/w/${workspaceId}`;

async function importCsv(page: Page, kind: string, file: string) {
  await page.goto(`${base()}/import`);
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

test("1. Import: preview shows errors, duplicates and unknown values; nothing is stored before confirmation", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example");
  workspaceId = await openWorkspace(page, /E2E Leerer Workspace/);
  await expect(page.getByText("Dieser Workspace ist noch leer.")).toBeVisible();

  // invalid upload: a PDF disguised as CSV is rejected with a clear message
  await page.goto(`${base()}/import`);
  await page.getByTestId("import-kind").selectOption("customers");
  await page.getByTestId("import-file").setInputFiles(fixture("getarnt.csv"));
  await page.getByTestId("import-upload").click();
  await expect(page.locator(".notice.danger").first()).toContainText(/passt nicht zur Endung|nicht verwendbar|Dateiinhalt/i);

  // erroneous CSV: the wizard shows row errors, the commit stays disabled (no silent partial import)
  await importCsv(page, "customers", "kunden-fehlerhaft.csv");
  await expect(page.getByTestId("import-preview-card")).toContainText("Fehler");
  await expect(page.getByTestId("import-preview-card")).toContainText(/Land|Betrag/);
  await expect(page.getByTestId("import-commit")).toBeDisabled();
  await expect(page.locator(".notice.danger").first()).toContainText("nicht bestätigt");

  // a scan without text layer is reported as such (no invented OCR text)
  await page.goto(`${base()}/import`);
  await page.getByTestId("import-kind").selectOption("document");
  await page.getByTestId("import-file").setInputFiles(fixture("scan-ohne-text.pdf"));
  await page.getByTestId("import-upload").click();
  await page.getByTestId("import-preview").click();
  await expect(page.getByTestId("import-preview-card")).toContainText("Keine Textebene");
  await expect(page.getByTestId("import-commit")).toBeDisabled();

  // nothing was stored so far
  const customers = await page.request.get(`/api/v1/workspaces/${workspaceId}/customers`);
  expect((await customers.json()).total).toBe(0);

  // valid imports: customers -> opportunities -> feedback (with deliberate duplicates)
  await importCsv(page, "customers", "kunden.csv");
  await expect(page.getByTestId("import-preview-card")).toContainText("8 neu");
  await commitImport(page);
  await importCsv(page, "opportunities", "opportunities.csv");
  await commitImport(page);
  await importCsv(page, "feedback", "feedback.csv");
  await expect(page.getByTestId("import-preview-card")).toContainText("2 Dubletten");
  await commitImport(page);
  await expect(page.getByTestId("import-result")).toContainText("19 neu");
  await expect(page.getByTestId("import-result")).toContainText("2 Dubletten übersprungen");

  // the same file again is recognised as already imported and cannot be committed twice
  await importCsv(page, "feedback", "feedback.csv");
  await expect(page.getByTestId("import-commit")).toBeDisabled();
  await expect(page.locator(".notice.danger").first()).toContainText("nichts zu importieren");

  // text note with paragraph locators
  await page.goto(`${base()}/import`);
  await page.getByRole("tab", { name: "Gesprächsnotiz einfügen" }).click();
  await page.getByLabel("Titel").fill("Jour fixe Notiz");
  await page.getByLabel("Text", { exact: true }).fill(readFileSync(fixture("gespraechsnotiz.txt"), "utf-8"));
  await page.getByLabel("Kunden-ID (optional)").fill("K-1001");
  await page.getByRole("button", { name: "Prüfen" }).click();
  await commitImport(page);

  // dashboard figures come from the stored data
  await page.goto(base());
  await expect(page.getByText("Kunden", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("kpi-arr")).toContainText("€");
  await expect(page.getByTestId("kpi-arr")).toContainText("CHF"); // two currencies, shown separately
  await context.close();
});

test("2. Analyse -> evidence -> correction (split/merge) -> initiative -> decision draft", async ({ browser }) => {
  const { page, context } = await newSession(browser, "bob@lumen.example", `/`);
  await openWorkspace(page, /E2E Leerer Workspace/);

  // analysis job (Demo-KI): visible progress, proposals appear, flagged as demo
  await page.goto(`${base()}/problems`);
  await expect(page.getByText("Noch keine Probleme")).toBeVisible();
  await page.getByTestId("start-analysis").click();
  await expect(page.getByTestId("job-progress")).toHaveAttribute("data-status", "succeeded", { timeout: 120_000 });
  const rows = page.getByTestId("problem-table").locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThanOrEqual(2);
  await expect(page.getByTestId("problem-table")).toContainText("Demo-KI");

  // open the export problem and an original evidence in the side panel
  await page.getByTestId("problem-table").getByRole("link", { name: /export/i }).first().click();
  await expect(page.getByTestId("problem-title")).toContainText(/export/i);
  await page.getByTestId("open-evidence").first().click();
  await expect(page.getByTestId("evidence-text")).toBeVisible();
  await expect(page.getByTestId("evidence-text").locator("mark")).toBeVisible();   // verbatim quote highlighted
  await expect(page.getByTestId("evidence-locator")).toContainText(/Zeile|Absatz/);   // traceable position in the file (CSV row or paragraph of a note)

  // unique customers vs statements
  await page.getByRole("tab", { name: /Kennzahlen/ }).click();
  const customers = Number((await page.getByTestId("metric-customers").locator(".value").textContent())!.trim());
  await expect(page.getByTestId("metric-customers")).toContainText("Aussagen insgesamt");
  const statementsText = await page.getByTestId("metric-customers").locator(".small").textContent();
  const statements = Number(statementsText!.match(/(\d+) Aussagen/)![1]);
  expect(statements).toBeGreaterThan(customers);
  await expect(page.getByTestId("metric-arr")).toContainText("€");

  // correction 1: change a relation (history entry), correction 2: split two pieces of evidence off and merge them back
  await page.getByRole("tab", { name: /Belege/ }).click();
  await page.getByTestId("open-evidence").nth(1).click();
  await page.getByTestId("evidence-relation").selectOption("context");
  await expect(page.getByText("Beleg aktualisiert.")).toBeVisible();
  const boxes = page.getByRole("checkbox", { name: "Beleg auswählen" });
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await page.getByTestId("split-open").click();
  await page.getByTestId("split-title").fill("Abgespaltener Export-Teil");
  await page.getByTestId("split-confirm").click();
  await expect(page.getByTestId("problem-title")).toHaveText("Abgespaltener Export-Teil");
  await page.getByRole("tab", { name: "Verlauf" }).click();
  await expect(page.locator("strong", { hasText: "durch Abspaltung entstanden" }).first()).toBeVisible();

  // merge both back through the list (original first = merge target)
  await page.goto(`${base()}/problems`);
  const table = page.getByTestId("problem-table");
  await table.locator("tbody tr").filter({ hasText: /export|csv/i }).filter({ hasNotText: "Abgespaltener" }).first().getByRole("checkbox").check();
  await page.getByRole("checkbox", { name: "Abgespaltener Export-Teil auswählen" }).check();
  await page.getByTestId("merge-open").click();
  await page.getByTestId("merge-confirm").click();
  await expect(page.getByTestId("problem-title")).not.toHaveText("Abgespaltener Export-Teil");
  await page.getByRole("tab", { name: "Verlauf" }).click();
  await expect(page.locator("strong", { hasText: "Probleme zusammengeführt" }).first()).toBeVisible();
  await expect(page.locator("strong", { hasText: "Beziehung korrigiert" }).first()).toBeVisible();
  await expect(page.locator("strong", { hasText: "Belege abgespalten" }).first()).toBeVisible();

  // confirm + rename the problem, then create the initiative
  await page.getByRole("button", { name: "Bearbeiten" }).click();
  await page.getByTestId("edit-title").fill("CSV-Export bricht bei großen Tabellen ab");
  await page.getByTestId("edit-save").click();
  await expect(page.getByTestId("problem-title")).toHaveText("CSV-Export bricht bei großen Tabellen ab");
  await page.getByTestId("problem-confirm").click();
  await expect(page.getByText("Bestätigt").first()).toBeVisible();
  await page.getByTestId("new-initiative").click();
  await page.getByTestId("initiative-title").fill("Streaming-Export");
  await page.getByLabel("Gewünschtes Ergebnis").fill("Große Tabellen lassen sich zuverlässig exportieren.");
  await page.getByLabel("Aufwand von").fill("20");
  await page.getByLabel("Aufwand bis").fill("35");
  await page.getByTestId("initiative-create").click();
  await expect(page.getByTestId("initiative-heading")).toHaveText("Streaming-Export");

  // assumptions: an observed one needs a source, an estimate does not
  await page.getByTestId("add-assumption").click();
  await page.getByTestId("assumption-statement").fill("Abbrüche entstehen ab etwa 50.000 Zeilen.");
  await page.getByTestId("assumption-kind").selectOption("observed");
  await expect(page.getByTestId("assumption-save")).toBeDisabled();
  await page.getByLabel("Quelle (Beleg des Problems)").selectOption({ index: 1 });
  await page.getByTestId("assumption-save").click();
  await page.getByTestId("add-assumption").click();
  await page.getByTestId("assumption-statement").fill("Umsetzung in 20 bis 35 Personentagen.");
  await page.getByTestId("assumption-kind").selectOption("estimate");
  await page.getByTestId("assumption-save").click();
  await expect(page.getByText("Umsetzung in 20 bis 35 Personentagen.")).toBeVisible();
  await expect(page.getByTestId("initiative-score")).toBeVisible();

  // decision draft with two options, snapshot, scoring, AI draft (demo), submit
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
  await page.getByTestId("decision-save").click();
  await expect(page.getByText("Entwurf gespeichert.")).toBeVisible();
  await expect(page.getByTestId("decision-submit")).toBeDisabled();   // snapshot still missing
  await page.getByTestId("snapshot-refresh").click();
  await expect(page.getByTestId("scoring-card")).toBeVisible();
  await expect(page.getByTestId("scoring-card")).toContainText("Streaming-Export");
  await expect(page.getByTestId("scoring-card")).toContainText("Rangfolge");
  await page.getByTestId("ai-draft-request").click();
  await expect(page.getByTestId("ai-draft")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("ai-draft")).toContainText("Demo-Entwurf");
  await page.getByTestId("decision-submit").click();
  await expect(page.getByTestId("decision-state")).toContainText("In Prüfung");
  await expect(page.getByTestId("decision-approve")).toHaveCount(0);   // an editor cannot approve
  await page.getByTestId("comment-body").fill("Bitte die Aufwandsschätzung prüfen.");
  await page.getByTestId("comment-add").click();
  await expect(page.getByTestId("comments")).toContainText("Bitte die Aufwandsschätzung prüfen.");
  process.env.E2E_DECISION_URL = page.url();
  await context.close();
});

test("3. Approval by a second person (admin), immutability, export with real data", async ({ browser }) => {
  const decisionUrl = process.env.E2E_DECISION_URL!;
  expect(decisionUrl).toContain("/decisions/");
  const { page, context } = await newSession(browser, "dora@lumen.example", decisionUrl.replace(/^https?:\/\/[^/]+/, ""));
  await expect(page.getByTestId("decision-state")).toContainText("In Prüfung");
  await expect(page.getByTestId("decision-approve")).toBeEnabled();
  await page.getByTestId("decision-approve").click();
  await expect(page.getByTestId("decision-state")).toContainText("Freigegeben");
  await expect(page.locator(".notice.ok", { hasText: "unveränderlich" })).toBeVisible();
  await expect(page.getByTestId("decision-save")).toHaveCount(0);
  await expect(page.getByTestId("decision-approve")).toHaveCount(0);

  // the API refuses changes to the approved revision
  const id = decisionUrl.match(/decisions\/([0-9a-f-]{36})/)![1]!;
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  const patch = await page.request.patch(`/api/v1/workspaces/${workspaceId}/decisions/${id}`, {
    data: { title: "Manipuliert" }, headers: { "X-CSRF-Token": me.csrf_token, "If-Match": '"1"' },
  });
  expect([409, 412]).toContain(patch.status());
  const noCsrf = await page.request.patch(`/api/v1/workspaces/${workspaceId}/decisions/${id}`, { data: { title: "x" }, headers: { "If-Match": '"1"' } });
  expect(noCsrf.status()).toBe(403);

  // export: real stored data (quotes from the imported feedback)
  const mdDownload = page.waitForEvent("download");
  await page.getByTestId("export-md").click();
  const md = readFileSync((await (await mdDownload).path())!, "utf-8");
  expect(md).toContain("Streaming-Export");
  expect(md).toContain("Freigegeben");
  expect(md).toMatch(/Eindeutige Kunden: \*\*\d+\*\*/);
  expect(md).toContain("keine Roadmap-Zusage");
  expect(md).toMatch(/Export|CSV/);
  const csvDownload = page.waitForEvent("download");
  await page.getByTestId("export-csv").click();
  const csv = readFileSync((await (await csvDownload).path())!, "utf-8").replace(/^﻿/, "");
  const lines = csv.trim().split(/\r?\n/);
  expect(lines[0]).toContain("entscheidung_id");
  expect(lines.length).toBeGreaterThan(5);
  expect(csv).toContain(id);

  // audit shows the approval and the exports (admin only)
  await page.goto(`${base()}/audit`);
  await expect(page.getByTestId("audit-table")).toContainText("decision.approved");
  await expect(page.getByTestId("audit-table")).toContainText("export.decision");
  await context.close();
});
