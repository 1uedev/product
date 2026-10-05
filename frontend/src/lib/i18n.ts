// Message catalogue for the shared UI vocabulary. German is the only shipped locale; additional locales add a catalogue
// of the same shape (workspace.locale selects it). Page specific prose lives next to the page.
export type Locale = "de-DE";

const de = {
  appName: "decision-evidence",
  nav: {
    overview: "Übersicht", import: "Import", sources: "Quellen & Suche", customers: "Kunden", problems: "Probleme", initiatives: "Initiativen",
    compare: "Vergleich", decisions: "Entscheidungen", jobs: "Aufgaben", members: "Mitglieder", audit: "Audit", settings: "Einstellungen",
  },
  common: {
    loading: "Lädt …", retry: "Erneut versuchen", save: "Speichern", cancel: "Abbrechen", close: "Schließen", delete: "Löschen", edit: "Bearbeiten",
    back: "Zurück", next: "Weiter", yes: "Ja", no: "Nein", none: "–", unknown: "unbekannt", search: "Suchen", reset: "Zurücksetzen", all: "Alle",
    forbidden: "Dafür fehlt Ihnen die Berechtigung.", loadError: "Die Daten konnten nicht geladen werden.", empty: "Noch keine Einträge.",
    requestId: "Anfrage-ID", signOut: "Abmelden", signIn: "Anmelden", workspace: "Workspace", demo: "Demo", page: "Seite", of: "von",
  },
  roles: { owner: "Owner", admin: "Admin", editor: "Editor", viewer: "Viewer" } as Record<string, string>,
  status: {
    problem: { proposed: "Vorgeschlagen", confirmed: "Bestätigt", archived: "Archiviert" } as Record<string, string>,
    initiative: { proposed: "Vorgeschlagen", evaluating: "In Bewertung", decided: "Entschieden", dropped: "Verworfen" } as Record<string, string>,
    decision: { draft: "Entwurf", in_review: "In Prüfung", approved: "Freigegeben", superseded: "Abgelöst" } as Record<string, string>,
    job: { queued: "In Warteschlange", running: "Läuft", succeeded: "Fertig", failed: "Fehlgeschlagen" } as Record<string, string>,
    import: { uploaded: "Hochgeladen", previewed: "Vorschau bereit", committing: "Wird importiert", committed: "Importiert", failed: "Fehlgeschlagen" } as Record<string, string>,
  },
  relation: { supports: "unterstützt", contradicts: "widerspricht", context: "Kontext" } as Record<string, string>,
  jobKind: { import_commit: "Import", analyze_feedback: "Analyse", draft_rationale: "KI-Entwurf" } as Record<string, string>,
  channel: { email: "E-Mail", ticket: "Ticket", call: "Gespräch", interview: "Interview", chat: "Chat", survey: "Umfrage", document: "Dokument", other: "Sonstiges" } as Record<string, string>,
  valueBasis: { arr: "ARR", annual_sales: "Jahresumsatz", unknown: "unbekannt" } as Record<string, string>,
  stage: { open: "Offen", won: "Gewonnen", lost: "Verloren", no_decision: "Keine Entscheidung" } as Record<string, string>,
  origin: { original: "Original", imported: "Importiert", synthetic: "Synthetisch" } as Record<string, string>,
  criteria: {
    customer_reach: "Kundenreichweite", arr_exposure: "ARR-Betroffenheit", pipeline_at_stake: "Pipeline im Spiel", evidence_quality: "Belegqualität",
    consistency: "Widerspruchsfreiheit", low_effort: "Geringer Aufwand", low_risk: "Geringes Risiko",
  } as Record<string, string>,
};

export type Messages = typeof de;
export const messages: Record<Locale, Messages> = { "de-DE": de };
export const t: Messages = messages["de-DE"];
export function label(map: Record<string, string>, key: string | null | undefined): string {
  if (!key) return t.common.none;
  return map[key] ?? key;
}
