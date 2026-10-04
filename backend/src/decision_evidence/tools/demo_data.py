"""Synthetic, deterministic demo data (never real customers). Used by the demo seed and to write the test fixtures.

Everything produced here is explicitly synthetic: names are invented, texts are generated from templates.
Deliberately included: duplicate tickets, counter-evidence, customers without revenue, two currencies, old evidence,
and one very loud customer (so "10 tickets are not 10 customers" is visible in the data).
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass
from datetime import date, timedelta

ANCHOR = date(2026, 9, 30)

# external_id, name, segment, country, value, basis, currency, as_of
CUSTOMERS: list[tuple[str, str, str, str, int | None, str, str | None, str | None]] = [
    ("K-1001", "Alpenwerk Maschinenbau GmbH", "enterprise", "DE", 420000, "arr", "EUR", "2026-06-30"),
    ("K-1002", "Rheinische Logistik AG", "enterprise", "DE", 380000, "arr", "EUR", "2026-06-30"),
    ("K-1003", "Helvetia Präzision AG", "enterprise", "CH", 310000, "arr", "CHF", "2026-06-30"),
    ("K-1004", "Nordsee Energie GmbH", "mid-market", "DE", 1200000, "annual_sales", "EUR", "2025-12-31"),
    ("K-1005", "Tirol Hotellerie GmbH", "mid-market", "AT", 96000, "arr", "EUR", "2026-06-30"),
    ("K-1006", "Baltic Foods B.V.", "mid-market", "NL", 88000, "arr", "EUR", "2026-03-31"),
    ("K-1007", "Zürich Treuhand AG", "mid-market", "CH", 150000, "arr", "CHF", "2026-06-30"),
    ("K-1008", "Kurpfalz Pharma GmbH", "enterprise", "DE", 510000, "arr", "EUR", "2026-06-30"),
    ("K-1009", "Donauland Bau GmbH", "smb", "AT", 24000, "arr", "EUR", "2026-06-30"),
    ("K-1010", "Schwarzwald Optik GmbH", "smb", "DE", 18000, "arr", "EUR", "2026-06-30"),
    ("K-1011", "Emsland Agrar eG", "smb", "DE", None, "unknown", None, None),
    ("K-1012", "Bern Mobility AG", "mid-market", "CH", None, "unknown", None, None),
    ("K-1013", "Hanse Reederei GmbH", "mid-market", "DE", 2500000, "annual_sales", "EUR", "2025-12-31"),
    ("K-1014", "Wallonie Textile S.A.", "smb", "BE", None, "unknown", None, None),
    ("K-1015", "Franken Medizintechnik GmbH", "enterprise", "DE", 275000, "arr", "EUR", "2026-06-30"),
    ("K-1016", "Lausanne Digital SA", "smb", "CH", 30000, "arr", "CHF", "2026-06-30"),
    ("K-1017", "Sachsen Stahl AG", "mid-market", "DE", 120000, "arr", "EUR", "2026-06-30"),
    ("K-1018", "Salzburg Verlag GmbH", "smb", "AT", 15000, "arr", "EUR", "2026-06-30"),
]

OPENINGS = ["", "Hallo, ", "Kurze Rückmeldung: ", "Aus dem letzten Jour fixe: ", "Zum Ticket von letzter Woche: ", "Wichtig für uns: "]
SUFFIXES = ["", " Das kostet uns jede Woche Zeit.", " Bitte um Rückmeldung zum Stand.", " Das haben wir schon mehrfach gemeldet.",
            " Im Quartalsabschluss ist das besonders kritisch.", " Unser Management fragt regelmäßig danach."]


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    description: str
    supports: tuple[str, ...]
    counters: tuple[str, ...]


THEMES: dict[str, Theme] = {t.key: t for t in [
    Theme("export", "CSV-Export bricht bei großen Datenmengen ab", "Große Tabellen lassen sich nicht zuverlässig als CSV exportieren.", (
        "Der CSV-Export bricht bei großen Tabellen mit mehr als 50.000 Zeilen ab.",
        "Beim Export nach CSV kommt nach etwa zwei Minuten ein Timeout und die Datei ist unvollständig.",
        "Wir exportieren jede Woche den Bestand als CSV, aber der Export bricht regelmäßig ab und wir müssen Zeilen manuell nachladen.",
        "Die exportierte CSV-Datei enthält nicht alle Spalten, die wir im Dashboard sehen.",
        "Für unsere Buchhaltung brauchen wir einen verlässlichen CSV-Export, aktuell bricht er bei großen Mengen ohne Fehlermeldung ab.",
        "Der Export großer Tabellen als CSV dauert ewig und scheitert mit einem Timeout.",
        "Beim CSV-Export mit Filtern fehlen Zeilen, wir müssen jede Datei nachzählen.",
        "Ein Export in mehreren Teilen oder als Stream würde uns helfen, denn der CSV-Export bricht bei vielen Zeilen ab."), (
        "Der CSV-Export funktioniert bei uns einwandfrei, wir haben damit kein Problem.",
        "Mit dem Export sind wir zufrieden, auch große Tabellen laufen stabil.",
        "Beim Export gibt es bei uns keine Schwierigkeiten.")),
    Theme("sso", "Fehlende SSO-Anbindung per SAML", "Unternehmenskunden verlangen Single Sign-on per SAML.", (
        "Ohne SAML-Anbindung für Single Sign-on dürfen wir die Software intern nicht ausrollen.",
        "Unsere IT verlangt SSO über SAML mit unserem Identity Provider, ein Passwort-Login reicht nicht.",
        "Die Anmeldung per SSO fehlt, das ist für unsere Sicherheitsrichtlinie ein Ausschlusskriterium.",
        "Wir brauchen SAML-SSO und automatische Nutzerprovisionierung, sonst scheitert die Freigabe durch die Informationssicherheit.",
        "Im Ausschreibungsverfahren war SSO per SAML eine Muss-Anforderung, die wir nicht erfüllen konnten.",
        "Mitarbeiter sollen sich mit dem Firmenkonto anmelden können, SAML-SSO ist dafür Voraussetzung.",
        "Der Sicherheitsbeauftragte fragt nach SSO und Anmeldeprotokollen, ohne SAML geht es nicht weiter."), (
        "SSO brauchen wir nicht, unsere Nutzer melden sich gern mit Passwort an.",
        "Die Anmeldung funktioniert bei uns gut, ein Single Sign-on ist kein Thema.")),
    Theme("mobile", "Keine mobile App mit Offlinezugriff", "Außendienst und Techniker brauchen Zugriff ohne Netz.", (
        "Unser Außendienst braucht eine mobile App, im Kundengespräch haben wir oft kein Netz.",
        "Es gibt keinen Offline-Modus, auf Baustellen ohne Empfang können wir keine Daten erfassen.",
        "Die mobile Ansicht im Browser ist zu langsam, eine native App mit Offline-Synchronisation wäre ideal.",
        "Techniker vor Ort wollen Berichte mobil ausfüllen und später synchronisieren, offline geht das bisher nicht.",
        "Wir hätten gern eine App für Smartphone und Tablet mit Offlinezugriff auf die letzten Aufträge.",
        "Ohne mobile App erfassen unsere Mitarbeiter alles zweimal, erst auf Papier, dann im System."), (
        "Eine mobile App brauchen wir nicht, bei uns arbeitet jeder am Schreibtisch.",
        "Mobil nutzen wir kaum etwas, der Browser reicht uns völlig.")),
    Theme("reports", "Berichte lassen sich nicht individuell anpassen", "Standardberichte sind zu starr, Kunden bauen Auswertungen extern nach.", (
        "Wir können Berichte nicht nach eigenen Kennzahlen zusammenstellen, die Vorlagen sind zu starr.",
        "Individuelle Dashboards mit gespeicherten Filtern fehlen, jede Woche bauen wir die Auswertung in Excel nach.",
        "Der Berichtsgenerator erlaubt keine eigenen Spalten und keine Gruppierung nach Region.",
        "Für die Geschäftsleitung brauchen wir anpassbare Berichte, die automatisch per Mail kommen.",
        "Gespeicherte Filter und frei konfigurierbare Auswertungen wären der größte Fortschritt für unser Controlling.",
        "Die Standardberichte passen nicht zu unseren Kennzahlen, wir brauchen einen flexiblen Berichtseditor."), (
        "Die Standardberichte reichen uns völlig aus, wir sind damit zufrieden.",)),
    Theme("api", "API-Limits und unzuverlässige Webhooks", "Integrationen stoßen an Rate-Limits, Webhooks gehen verloren.", (
        "Das API-Ratelimit von 60 Anfragen pro Minute ist für unsere Integration viel zu niedrig.",
        "Webhooks werden bei Fehlern nicht wiederholt, dadurch gehen Ereignisse in unserem ERP verloren.",
        "Wir stoßen ständig auf das Rate-Limit der API und müssen Anfragen künstlich drosseln.",
        "Die Webhook-Zustellung ist unzuverlässig, es fehlt eine Wiederholung und ein Zustellprotokoll.",
        "Für den Datenabgleich mit unserem ERP brauchen wir höhere API-Limits und zuverlässige Webhooks.",
        "Ereignisse kommen per Webhook doppelt oder gar nicht an, die API-Dokumentation sagt dazu nichts."), (
        "Die API und die Webhooks laufen bei uns stabil, das Rate-Limit stört uns nicht.",)),
    Theme("permissions", "Rechteverwaltung ist zu grob", "Feinere Rollen und Berechtigungen pro Projekt werden verlangt.", (
        "Die Rollen sind zu grob, wir brauchen Berechtigungen pro Abteilung und Projekt.",
        "Es gibt nur Admin und Nutzer, feinere Rechte pro Modul fehlen für unsere Revision.",
        "Unser Datenschutzbeauftragter verlangt, dass Teams nur ihre eigenen Datensätze sehen, die Rechteverwaltung kann das nicht.",
        "Wir würden gern Leserechte für Externe vergeben ohne Schreibzugriff, die Rollenverwaltung ist dafür zu grob.",
        "Berechtigungen pro Projekt und eine Rechteübersicht für Audits fehlen."), (
        "Die Rollen genügen uns, wir brauchen keine feinere Rechteverwaltung.",)),
    Theme("billing", "Rechnungsstellung unvollständig", "Rechnungen enthalten falsche oder fehlende Angaben.", (
        "Die Rechnungen kommen mit falscher Adresse und wir müssen jede Rechnung korrigieren lassen.",
        "Die Rechnung enthält keine Bestellnummer, unsere Buchhaltung lehnt sie deshalb ab.",
        "Wir bekommen Rechnungen zu spät und ohne Leistungszeitraum.",
        "Die Rechnung weist die Umsatzsteuer falsch aus, die Korrektur dauert Wochen.",
        "Eine Sammelrechnung mit Bestellnummern pro Position wäre für unsere Rechnungsprüfung wichtig."), ()),
]}

NOISE = [
    "Danke für den freundlichen Kontakt im letzten Workshop, das Team war sehr hilfsbereit.",
    "Die Schulung am Dienstag war gut organisiert und hat unseren Einsteigern geholfen.",
    "Bitte denkt an den Termin für das Jahresgespräch im November.",
    "Unser neuer Ansprechpartner im Einkauf ist ab Oktober Herr Vogel.",
    "Wir überlegen, im nächsten Jahr weitere Lizenzen für die Niederlassung zu bestellen.",
    "Der Relaunch eurer Webseite sieht übersichtlich aus.",
    "Gibt es eine Roadmap-Präsentation für Kunden im Frühjahr?",
    "Für die Teilnahme am Beirat melden wir uns noch mit Terminvorschlägen.",
]

# (customer index 1-based, theme key) -> (supports, counters)
FULL_ASSIGNMENTS: dict[tuple[int, str], tuple[int, int]] = {
    (1, "export"): (14, 0), (2, "export"): (3, 0), (4, "export"): (3, 0), (8, "export"): (2, 0), (13, "export"): (3, 0), (17, "export"): (2, 0),
    (10, "export"): (1, 0), (3, "export"): (0, 1), (15, "export"): (0, 1),
    (2, "sso"): (3, 0), (8, "sso"): (4, 0), (15, "sso"): (3, 0), (1, "sso"): (2, 0), (3, "sso"): (3, 0), (17, "sso"): (1, 0), (7, "sso"): (2, 0), (9, "sso"): (0, 1),
    (13, "mobile"): (3, 0), (5, "mobile"): (3, 0), (9, "mobile"): (2, 0), (12, "mobile"): (2, 0), (6, "mobile"): (2, 0), (14, "mobile"): (1, 0),
    (1, "mobile"): (0, 1), (8, "mobile"): (0, 1),
    (4, "reports"): (3, 0), (6, "reports"): (2, 0), (7, "reports"): (2, 0), (10, "reports"): (2, 0), (11, "reports"): (2, 0), (17, "reports"): (1, 0),
    (18, "reports"): (1, 0), (16, "reports"): (1, 0), (2, "reports"): (0, 1),
    (2, "api"): (2, 0), (15, "api"): (2, 0), (8, "api"): (2, 0), (3, "api"): (1, 0), (1, "api"): (1, 0), (12, "api"): (0, 1),
    (8, "permissions"): (2, 0), (15, "permissions"): (1, 0), (7, "permissions"): (1, 0), (4, "permissions"): (1, 0), (2, "permissions"): (0, 1),
    (5, "billing"): (2, 0), (6, "billing"): (2, 0), (9, "billing"): (1, 0), (18, "billing"): (1, 0), (11, "billing"): (1, 0),
}
SMALL_ASSIGNMENTS: dict[tuple[int, str], tuple[int, int]] = {
    (1, "export"): (6, 0), (2, "export"): (2, 0), (4, "export"): (2, 0), (3, "export"): (0, 1),
    (2, "sso"): (2, 0), (8, "sso"): (3, 0), (15, "sso"): (2, 0), (9, "sso"): (0, 1),
}
SMALL_CUSTOMERS = (1, 2, 3, 4, 8, 9, 15, 17)

CHANNELS = ["ticket", "email", "call", "interview", "chat", "survey"]
THEME_AGE = {"export": (3, 150), "sso": (10, 300), "mobile": (20, 380), "reports": (5, 260), "api": (200, 420), "permissions": (190, 400), "billing": (2, 60)}


@dataclass
class Statement:
    customer_ext: str
    theme: str | None
    relation: str          # supports | contradicts | context
    text: str
    channel: str
    day: date
    opportunity_ext: str | None
    external_id: str


@dataclass
class DemoDataset:
    customers_csv: str
    opportunities_csv: str
    feedback_csv: str
    statements: list[Statement]
    customer_rows: list[tuple]
    opportunity_rows: list[tuple]
    duplicate_rows: int


def _csv(header: list[str], rows: list[list[object]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _opportunities(customers: list[tuple]) -> list[tuple]:
    rng = random.Random(7)
    stages = ["open", "open", "lost", "won", "open", "no_decision", "lost", "open"]
    out: list[tuple] = []
    n = 0
    for c in customers:
        for k in range(2):
            n += 1
            ext = f"O-{2000 + n}"
            stage = stages[(n + k) % len(stages)]
            cur = c[6] or ("EUR" if c[3] in ("DE", "AT", "NL", "BE") else "CHF")
            amount: int | None = rng.choice([18000, 24000, 36000, 52000, 75000, 120000, 180000, 240000])
            if c[0] == "K-1013" and k == 1:
                cur = "USD"
            if n % 11 == 0:
                amount = None                       # unknown amount stays unknown
            closed = None if stage == "open" else (ANCHOR - timedelta(days=rng.randint(10, 260))).isoformat()
            out.append((ext, c[0], f"{c[1].split()[0]} {['Erweiterung', 'Neukunden-Lizenz', 'Verlängerung', 'Add-on'][k + (n % 2)]}", stage,
                        "" if amount is None else amount, "" if amount is None else cur, closed or ""))
    return out


def generate(profile: str = "full", seed: int = 42) -> DemoDataset:
    rng = random.Random(seed)
    assignments = FULL_ASSIGNMENTS if profile == "full" else SMALL_ASSIGNMENTS
    customers = [c for i, c in enumerate(CUSTOMERS, start=1) if profile == "full" or i in SMALL_CUSTOMERS]
    opps = _opportunities(customers)
    opps_by_customer: dict[str, list[tuple]] = {}
    for o in opps:
        opps_by_customer.setdefault(o[1], []).append(o)

    statements: list[Statement] = []
    used: set[tuple[str, str]] = set()
    counter = 0

    def make(customer_idx: int, theme: Theme, pool: tuple[str, ...], relation: str) -> None:
        nonlocal counter
        cust = CUSTOMERS[customer_idx - 1]
        for _ in range(60):
            text = (rng.choice(OPENINGS) if rng.random() < 0.3 else "") + rng.choice(pool) + (rng.choice(SUFFIXES) if rng.random() < 0.3 else "")
            if (cust[0], text) not in used:
                break
        else:  # pragma: no cover - pool exhausted: make unique by a harmless ticket reference
            text = f"{rng.choice(pool)} (Referenz {rng.randint(1000, 9999)})"
        used.add((cust[0], text))
        lo, hi = THEME_AGE[theme.key]
        day = ANCHOR - timedelta(days=rng.randint(lo, hi))
        opp = None
        cand = [o for o in opps_by_customer.get(cust[0], []) if o[3] in ("open", "lost")]
        if cand and rng.random() < 0.45 and relation == "supports":
            opp = rng.choice(cand)[0]
        counter += 1
        statements.append(Statement(cust[0], theme.key, relation, text, rng.choice(CHANNELS), day, opp, f"T-{10000 + counter}"))

    for (cidx, tkey), (sup, con) in sorted(assignments.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        theme = THEMES[tkey]
        for _ in range(sup):
            make(cidx, theme, theme.supports, "supports")
        for _ in range(con):
            make(cidx, theme, theme.counters, "contradicts")
    if profile == "full":
        for i, text in enumerate(NOISE):
            cust = CUSTOMERS[(i * 5 + 2) % len(CUSTOMERS)]
            counter += 1
            statements.append(Statement(cust[0], None, "context", text, rng.choice(CHANNELS), ANCHOR - timedelta(days=rng.randint(5, 200)), None, f"T-{10000 + counter}"))
    rng.shuffle(statements)

    rows: list[list[object]] = []
    for st in statements:
        rows.append([st.external_id, st.customer_ext, st.opportunity_ext or "", st.channel, st.day.isoformat(), st.text, "de"])
    # deliberate duplicate tickets: the same customer sends the same text again on another day
    dup_src = [st for st in statements if st.relation == "supports"][:6 if profile == "full" else 2]
    for i, st in enumerate(dup_src):
        rows.append([f"T-{20000 + i}", st.customer_ext, "", "email", (st.day + timedelta(days=9)).isoformat(), st.text.upper() if i % 2 else f"  {st.text}  ", "de"])

    cust_rows = [[c[0], c[1], c[2], c[3], "" if c[4] is None else c[4], "" if c[4] is None else c[5], "" if c[6] is None else c[6], c[7] or ""] for c in customers]
    opp_rows = [list(o) for o in opps]
    return DemoDataset(
        customers_csv=_csv(["external_id", "name", "segment", "country", "commercial_value", "value_basis", "currency", "value_as_of"], cust_rows),
        opportunities_csv=_csv(["external_id", "customer_external_id", "name", "stage", "amount", "currency", "closed_at"], opp_rows),
        feedback_csv=_csv(["external_id", "customer_external_id", "opportunity_external_id", "channel", "occurred_at", "body", "language"], rows),
        statements=statements, customer_rows=customers, opportunity_rows=opps, duplicate_rows=len(dup_src))


NOTE_TEXT = """Gesprächsnotiz Jour fixe, Hanse Reederei

Die Disposition exportiert jeden Montag die Frachtliste als CSV. Der CSV-Export bricht dabei bei mehr als 50.000 Zeilen ab, deshalb wird die Liste von Hand in Teilen geladen.

Außerdem wurde nach einer mobilen App mit Offline-Modus für die Bordtechniker gefragt. Eine Zusage wurde nicht gegeben.
"""
