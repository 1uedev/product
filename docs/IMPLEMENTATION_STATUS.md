# Umsetzungsstand

Dieses Dokument ist so geschrieben, dass die Arbeit nach einem Kontextwechsel fortgesetzt werden kann. Es unterscheidet bewusst vier Stufen:

* **implementiert**: Code vorhanden und im Repository
* **im Demo geprüft**: durch automatische Tests gegen den vollständigen Compose-Stack mit Demo-KI belegt (Details und Zahlen: [VERIFICATION.md](VERIFICATION.md))
* **mit echtem Dienst geprüft**: gegen den echten externen Dienst (z. B. Anthropic API) gelaufen
* **blockiert / offen**: nicht umgesetzt oder nicht prüfbar, mit konkretem Grund

Der Demo-KI-Modus ist keine Bestätigung der Qualität eines echten Modells.

## Pflichtumfang (Stand: Abschluss der Umsetzung)

| Bereich | implementiert | im Demo geprüft | mit echtem Dienst geprüft | Anmerkung |
|---|---|---|---|---|
| Modularer Monolith, Docker-Betrieb (gateway, frontend, api, worker, outbox-publisher, postgres, keycloak, rabbitmq, storage, migrate/bootstrap) | ja | ja | ja (alles läuft real im Stack) | Nicht-Root-Prozesse, mehrstufige Dockerfiles, `.dockerignore`, Healthchecks |
| PostgreSQL 18, Migrationen, Constraints, RLS (ENABLE+FORCE), getrennte Rollen | ja | ja | ja | Rollen: migrator, auth, app, worker, outbox, recovery. Zusammengesetzte Mandanten-FKs |
| Mandantenisolation (IDs, Suche, Jobs, Downloads, Exporte, KI-Belege, Pool-Wiederverwendung, fehlender Kontext, Cross-Tenant-FK) | ja | ja (Integrationstests mit eingeschränkter Rolle) | ja | `tests/integration/test_rls.py`, `test_api_security.py`, `test_schema.py` |
| OIDC (Keycloak, PKCE, State, Nonce), serverseitige Sitzungen, CSRF, Rollen, Einladungen, Workspace-Wechsel, Logout | ja | ja (echte Anmeldung im Browser) | ja | Keine Mocks im Browser-Test. SAML, SCIM, MFA-Konfiguration nicht umgesetzt |
| Jobs mit transaktionaler Outbox, Publisher mit Bestätigung, Lease, Retry, Wiederherstellung | ja | ja (Brokerausfall, Worker-Kill, Neustarts) | ja | `90-resilience.spec.ts`, `tests/integration/test_jobs.py` |
| Objektspeicher (S3-Protokoll, SeaweedFS), Aufräumen verwaister Dateien | ja | ja | ja | |
| Import: CSV (Kunden, Chancen, Feedback), Text-Notizen, Mapping, Vorschau, Fehler, Dubletten, Commit als Job | ja | ja | ja | Kein stiller Teilimport; Beispiele in `docs/examples` |
| Analyse: Problemcluster vorschlagen, serverseitige Belegprüfung | ja | ja (Demo-KI) | **nein** | Anthropic-Adapter nur gegen SDK-Stub geprüft, Ollama siehe nächste Zeile |
| Lokales Sprachmodell über Ollama (`AI_PROVIDER=ollama`), Compose-Overlay, Fehlerbilder, Größenprüfung des Kontextfensters | ja | ja (Fehlerpfade gegen simulierten Server und gegen einen echten Ollama-Server ohne Modell, ganzer Weg bis zur Oberfläche) | **nein** | keine Textgenerierung mit einem echten Modell getestet (Modell-Download gesperrt), siehe [Ollama-Betrieb](operations/ollama.md) |
| Cluster korrigieren: zusammenführen, aufteilen, Belege verschieben/entfernen, Beziehung ändern, Historie | ja | ja | ja | Optimistische Sperre (`If-Match`) |
| Kennzahlen: eindeutige Kunden, Aussagen, Segmente, Gegenbelege, ARR/Umsatz/Pipeline getrennt, Währungen getrennt, unbekannt ≠ 0, Dedup im Portfolio, Alter/Abdeckung | ja | ja | ja | `tests/unit/test_metrics.py`, Integrationsfluss |
| Scoring: Policy (Gewichte, Fehlwertregel), Vergleich, Sensitivität ±25 % | ja | ja | ja | Deterministisch, ohne KI |
| Initiativen mit Annahmen und Optionen, Vergleichsansicht | ja | ja | ja | |
| Entscheidungsdokumente: Revisionen, Snapshot, Drift, Kommentare, Vier-Augen-Freigabe, Unveränderlichkeit, Neuversion | ja | ja | ja | Unveränderlichkeit per Datenbanktrigger belegt |
| Export (Markdown, Belege-CSV, Bewertungs-CSV) mit Schutz vor Formelinjektion | ja | ja | ja | Kein JSON-Export, keine PDF-Ausgabe |
| Oberfläche (deutsch): Anmeldung, Workspace-Wechsel, Übersicht, Import, Quellen, Kunden/Chancen, Probleme mit Beleg-Panel, Initiativen, Vergleich, Entscheidungen, Jobs, Mitglieder, Einstellungen, Audit | ja | ja | ja | Lade-, Leer-, Fehler-, Berechtigungszustände, Tastaturbedienung, Fokus, responsiv; axe-Prüfung auf Kernseiten |
| Mitglieder- und Einladungsverwaltung, Audit-Log | ja | ja | ja | Einladungen als Link, keine E-Mail-Zustellung |
| Demo-Daten für zwei Mandanten, idempotenter Seed, Demo-Kennzeichnung | ja | ja | ja | Haupt-Workspace: 18 Kunden, 36 Chancen, über 100 Aussagen, 6 Probleme, 3 Initiativen |
| Strukturierte Logs, Request-/Job-Korrelation, Health live/ready, Konfigurationsprüfung beim Start | ja | ja | ja | |
| Produktionskonfiguration (`compose.prod.yaml`), Verweigerung unsicherer Werte | ja | ja (Konfigurationsprüfung; Smoke-Test im Produktionsmodus mit HTTPS) | ja (lokal, `tls internal`) | Öffentliche ACME-Zertifikate nicht geprüft (kein öffentlicher DNS-Name) |
| Backup und Restore (PostgreSQL, Keycloak-DB, Objekte), Restore in neues Projekt | ja | ja | ja | |
| Testisolation (`de-test-*`-Projekte, eigene Volumes) | ja | ja | ja | |
| Dokumentation (README, ER-Diagramm, OpenAPI, ADRs, Betrieb, KI-Datenfluss, Importbeispiele) | ja | | | siehe [README](../README.md) |
| CI-Konfiguration (`.github/workflows/ci.yml`) | ja | | | **nie in GitHub Actions ausgeführt**; die aufgerufenen Skripte liefen lokal |

## Bewusst nicht Teil des MVP (offene Erweiterungen)

Getrennt vom Pflichtumfang, ohne Anspruch auf Umsetzung:

1. Konnektoren (CRM, Ticketsysteme, Datenwarehouse) und Schreibzugriffe darauf.
2. Unternehmens-SSO über SAML, SCIM, Gruppenmapping; MFA-Vorgaben im Realm.
3. E-Mail-Zustellung für Einladungen und Benachrichtigungen.
4. Hochverfügbarkeit (replizierte Datenbank, mehrere API- und Worker-Instanzen), Cluster-Deployment.
5. Lasttests und darauf gestützte Kapazitätsangaben; Ratenbegrenzung der API.
6. Virenscan und erweiterte Dateiprüfung (PDF-Inhalte, Makros).
7. Embedding-basierte Ähnlichkeitssuche (siehe ADR 0007).
8. Mehrsprachige Oberfläche, JSON- und PDF-Export.
9. Aufbewahrungsfristen, Löschprozess für Betroffenenanfragen, Löschen ganzer Workspaces durch den Kunden.
10. Verschlüsselte, zeitgesteuerte Sicherungen und Point-in-Time-Recovery.
11. Prüfung des Anthropic-Adapters gegen die echte API und des Ollama-Adapters mit einem installierten Modell (Qualität der Cluster und Entwürfe, Laufzeit, Schema-Treue, Kosten, Rate Limits, Ablehnungen).
12. Zugangsschlüssel für einen Ollama-Server hinter einem authentifizierenden Proxy.

## Abweichungen von der Vorgabe

Dokumentiert in [ADR 0001](adr/0001-versionen-und-abweichungen.md). Es gibt keine Abweichung bei den Hauptversionen. Wesentlich ist: Fernsteuerung des Celery-Workers abgeschaltet, weil RabbitMQ 4.3 sie nicht mehr zulässt (Healthchecks über Heartbeat-Dateien).

## Wie man hier weitermacht

1. `README.md` lesen, `docker compose up --build -d`, mit den Demo-Zugängen anmelden.
2. Backend: `backend/src/decision_evidence` (Module siehe [Architekturüberblick](architecture/overview.md)); Fachregeln in `modules/metrics/domain.py` und `modules/scoring/domain.py`.
3. Schemaänderungen: neue Alembic-Revision unter `backend/migrations/versions` mit SQL unter `backend/migrations/sql`; danach `tests/integration/test_schema.py` ergänzen (jede `app`-Tabelle braucht Mandantenspalte, Policy, Tests).
4. API-Änderungen: `docs/openapi.json` und `frontend/src/api/schema.d.ts` neu erzeugen (Befehle in `.github/workflows/ci.yml`), CI vergleicht beide mit dem Code.
5. Vor jedem Abschluss `scripts/test_integration.sh` und `scripts/test_e2e.sh` ausführen und [VERIFICATION.md](VERIFICATION.md) mit den echten Ergebnissen aktualisieren.
