# Verifikation

Hier steht, was tatsächlich ausgeführt wurde, mit Befehl und Ergebnis, und was nicht geprüft werden konnte. Nichts in diesem Dokument ist geschätzt oder aus früheren Läufen übernommen:
die Ergebnisse stammen aus den Läufen auf dem Branch `claude/gifted-wozniak-kjzm8z` am 4. Oktober 2026. Die Tabelle gilt für den Stand mit Ollama-Unterstützung (Commit siehe Pull Request, Basis `e0ec281` plus Ollama-Adapter, Migration 0005, Compose-Overlay); die Läufe fanden auf dem unveränderten Arbeitsstand vor diesem Commit statt.

Umgebung: Linux 6.18 (Cloud-Container), Docker 29.6.2, Docker Compose 5.3.1, TLS-prüfender Ausgangsproxy (Images wurden mit `EXTRA_CA_BUNDLE` gebaut), Chromium aus `/opt/pw-browsers`.
Alle Docker-Läufe verwenden eigene Compose-Projekte `de-test-<zufall>` mit eigenen Volumes und Ports; die Skripte entfernen ausschließlich Projekte mit diesem Präfix.

## Ergebnisübersicht

| Prüfung | Befehl | Ergebnis |
|---|---|---|
| Backend Lint | `cd backend && uv run ruff check src ../tests` | bestanden (`All checks passed!`) |
| Backend Typen | `cd backend && uv run mypy src` | bestanden (85 Quelldateien, keine Befunde) |
| Backend Unit- und Integrationstests, echtes PostgreSQL 18, eingeschränkte Rollen | `scripts/test_integration.sh -q` | **149 bestanden** (80 Unit, 69 Integration), 3 übersprungen (Live-Tests gegen Ollama, benötigen `OLLAMA_TEST_URL`), 0 fehlgeschlagen, 37,7 s |
| Frontend Lint | `cd frontend && pnpm lint` | bestanden, keine Warnungen |
| Frontend Typen | `cd frontend && pnpm typecheck` | bestanden (keine Ausgabe von `tsc`) |
| Frontend Build | `cd frontend && pnpm build` | bestanden (Next.js 16.3.8, 18 Routen); zusätzlich bauen die Docker-Läufe das Produktionsimage |
| OpenAPI und Typen aktuell | `export_openapi` und `pnpm gen:api`, danach `git diff --exit-code` | bestanden (58 Pfade, keine Abweichung) |
| Browser-Tests, kompletter Stack, echte Keycloak-Anmeldung | `scripts/test_e2e.sh` | **15 bestanden, 2 übersprungen** (Produktions-Smoke-Test und Ollama-Test laufen separat), 3,6 min |
| Produktionsmodus: HTTPS, erster Nutzer, Einladung, Cookie | `scripts/test_prod_smoke.sh` | **bestanden** (1 Browser-Test, danach Readiness 200 und `/api/docs` 404) |
| Produktionskonfiguration ohne Container | `scripts/check_prod_config.sh` | bestanden (vollständige Umgebung akzeptiert, fehlende Geheimnisse abgelehnt, Ollama-Overlay wird gerendert) |
| Lokales Modell: Adapter gegen **echten Ollama-Server 0.35.1 ohne Modell** | `OLLAMA_TEST_URL=http://127.0.0.1:11434 uv run pytest ../tests/live` | 2 bestanden (Fehlerbild „Modell fehlt“ aus der echten 404-Antwort, nicht erreichbarer Server), 1 übersprungen (benötigt `OLLAMA_TEST_MODEL`) |
| Lokales Modell: ganzer Weg mit **echtem Ollama-Container** im Stack | `OLLAMA_OVERLAY=1 AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b scripts/test_e2e.sh 40-ollama` | bestanden (Oberfläche zeigt den Anbieter, die Analyse läuft über API, Outbox, Broker und Worker zum Ollama-Container, die Oberfläche zeigt `ai_model_missing` mit dem Hinweis `ollama pull`) |
| Backup und Restore in ein neues Projekt | `scripts/test_backup_restore.sh` | **bestanden** (Tabellenzählungen identisch, 8 Objekte, keine fehlenden und keine verwaisten Objekte, Browser-Prüfung gegen das wiederhergestellte Projekt) |
| Benutzerhandbuch: alle 56 Screenshots aus der laufenden Anwendung aufgenommen (frischer Teststack, echte Anmeldungen, kompletter Hauptablauf) und auf Vollständigkeit und Verweise geprüft | `playwright test -c playwright.screenshots.config.ts`, `pytest ../tests/unit/test_docs.py` | 9 von 9 Aufnahmeschritten bestanden, 2 Konsistenztests bestanden (Bilder und Kapitelverweise) |
| Compose-Dateien syntaktisch | `docker compose -f compose.yaml config -q`, mit `compose.test.yaml`, Produktionsdatei über `check_prod_config.sh` | bestanden |

## Was die Browser-Tests abdecken (`tests/e2e/specs`)

| Datei | Inhalt |
|---|---|
| `00-auth` | echte OIDC-Anmeldung (kein Mock), Workspace-Wahl und Wechsel, Abmelden, Sitzung danach ungültig, falsches Passwort, fremder Mandant liefert 404 |
| `10-main-flow` | **Hauptablauf** Import (Vorschau mit Fehlern, Dubletten, unbekannten Werten, nichts gespeichert vor Bestätigung) → Analyse → Beleg öffnen → Cluster korrigieren (aufteilen, zusammenführen) → Initiative → Entscheidungsentwurf → Freigabe durch zweite Person → Unveränderlichkeit → Export mit echten gespeicherten Daten |
| `20-access` | Viewer nur lesend (Oberfläche und Server), Editor kann nicht freigeben, Admin sieht Audit und Einstellungen, historische Entscheidung gegen neue Erkenntnisse, Einladungen (einmalig, an E-Mail gebunden, Rolle), letzter Owner geschützt |
| `30-a11y` | axe-Prüfung der Kernseiten (keine schweren oder kritischen Verstöße), Tastaturbedienung, Skip-Link, sichtbarer Fokus, Dialoge per Tastatur, kein horizontaler Scroll auf Telefonbreite |
| `90-resilience` | Brokerausfall (Job wartet in der Outbox, nach Rückkehr genau ein Ergebnis), Worker hart beendet während eines Jobs mit Prompt-Injection-Text (Lease läuft ab, Wiederherstellung beendet genau einmal), Daten überstehen Neustart von Datenbank, Speicher, API und Worker |
| `40-ollama` | läuft nur mit `E2E_AI_PROVIDER=ollama` (automatisch bei `OLLAMA_OVERLAY=1`): Anbieter in den Einstellungen, Analyse bis zum echten Ollama-Server, sichtbarer Fehler bei fehlendem Modell. Mit `E2E_OLLAMA_MODEL_INSTALLED=1` wird stattdessen ein Abschluss der Analyse erwartet |
| `99-prod-smoke` | läuft nur über `scripts/test_prod_smoke.sh`: Produktionsmodus, HTTPS mit interner CA, Einladung annehmen, `__Host-`-Cookie mit Secure, HttpOnly und SameSite=Lax, kein Demo-Inhalt |

## Was die Backend-Tests abdecken (`tests/unit`, `tests/integration`)

* **Mandantentrennung mit echtem PostgreSQL 18 und den eingeschränkten Laufzeitrollen** (nicht Superuser, nicht Eigentümer, kein BYPASSRLS): fremde IDs, Suche, Jobs, Downloads, Exporte, KI-Belege; zusammengesetzte Fremdschlüssel verhindern Verweise über Mandanten; ohne Mandantenkontext keine Zeilen; Pool-Wiederverwendung hinterlässt keinen Kontext; Trigger (letzter Owner, Audit nur anhängbar, unveränderliche Freigabe, Beleg-Konsistenz); Schema-Vollständigkeit (jede `app`-Tabelle mit RLS).
* **Fachregeln** (reine Funktionen und Integrationsfluss): eindeutige Kunden gegenüber Aussagen, Währungen getrennt, unbekannt ist nicht 0, Kundenwert im Portfolio nur einmal, Aktualität und Abdeckung, reproduzierbares Scoring und Sensitivität, Fehlwertregeln, Gewichtsvalidierung.
* **Sicherheit der API**: CSRF-Header und Origin, Rollen je Endpunkt, Einladungsregeln, Sitzungswiderruf, Konfigurationsverweigerung im Produktionsmodus.
* **OIDC-Bausteine** (21 Unit-Tests): Signatur, Issuer, Audience, Ablauf, Nonce, Umschreibung öffentlicher und interner Issuer.
* **Jobs**: Idempotenz, Claim mit Lease, Retry mit Backoff, verdrängter Worker kann nichts überschreiben, Outbox-Zustellung.
* **Lokales Modell** (`tests/unit/test_ollama.py`, 12 Tests; zwei Job-Tests in `tests/integration/test_jobs.py`): Anfrage ist ein schemabeschränkter Chat ohne Werkzeuge, maskierte Daten, Fehlerabbildung (nicht erreichbar, Zeitlimit, Modell fehlt, 5xx, 429, 401, 400, ungültige oder abgeschnittene Antwort, volles Kontextfenster), Größenprüfung vor dem Aufruf, Konfigurationsprüfung, erfundene Quellen und Zitate eines Modells werden verworfen, Analyse als Job speichert nur geprüfte Vorschläge und protokolliert Anbieter `ollama`, fehlendes Modell schlägt ohne Wiederholung sichtbar fehl.
* **KI-Prüfung** (Unit): fremde oder erfundene Chunk-IDs und nicht wörtliche Zitate werden verworfen; der Anthropic-Adapter wird gegen einen **Stub des SDK-Clients** geprüft (Anfrageform, Fehler, Ablehnung, Budget).
* **Import**: Zahlenformate, Kodierungen, Trennzeichen, Formelinjektion in Exporten, Dubletten, Fehlerzeilen, Größen- und Zeilenlimits.

## Während der Verifikation gefundene und behobene Fehler

Diese Läufe haben echte Mängel aufgedeckt, die erst dadurch auffielen:

1. **Keycloak-Healthcheck meldete zu früh „healthy“.** Die Prüfung suchte nach `"status": "UP"` und traf dabei eine Teilprüfung („Graceful Shutdown“) im Antworttext, obwohl Keycloak noch mit 503 antwortete. Folge: Dienste und Skripte starteten vor der Bereitschaft. Behoben: Prüfung auf die HTTP-Statuszeile 200 (`compose.yaml`, `compose.prod.yaml`). Entdeckt durch `kcadm.sh` im Produktions-Smoke-Test.
2. **Playwright-Hilfsfunktion zeigte ein Race.** Nutzer mit nur einem Workspace werden automatisch weitergeleitet, die Workspace-Wahl blitzte kurz auf und verschwand vor dem Klick. Der erste Lauf der Browser-Tests scheiterte deshalb an einem Test (`00-auth`), der zweite Lauf nach der Korrektur der Hilfsfunktion bestand alle. Es war ein Testfehler, kein Fehler der Anwendung.
3. **Dokumentierter Weg zum ersten Nutzer war unvollständig.** Ohne Vor- und Nachname fordert Keycloak sie bei der Anmeldung ab. Die Dokumentation und das Smoke-Skript legen den Nutzer jetzt vollständig an.
4. **CI-Schritt „Generated API types are up to date“** rief `git diff` aus dem falschen Verzeichnis auf (erster Lauf in GitHub Actions, Job `frontend`). Behoben und lokal nachgestellt.
5. **Veraltetes Image im Integrationsskript.** `scripts/test_integration.sh` führte die Migration mit einem zuvor gebauten Image aus und hätte neue Migrationen nicht angewendet; ein Test mit dem neuen Anbieterwert deckte das auf. Das Skript baut das Image jetzt vor dem Lauf (`run --build`).
6. Typfehler in `tools/admin.py` und ein unsortierter Import in `migrations/env.py` (mypy und ruff).

## Nicht ausgeführt oder nicht verifiziert (konkrete Grenzen)

| Prüfung | Grund |
|---|---|
| **Echter KI-Anbieter** | kein API-Schlüssel in dieser Umgebung. Der Anthropic-Adapter ist nur gegen einen Stub geprüft. Qualität der Cluster, Entwürfe, Kosten, Rate Limits und reale Ablehnungen sind unbekannt. Der Demo-KI-Modus ist keine Bestätigung der Qualität eines echten Modells |
| **Echte Textgenerierung mit einem Ollama-Modell** | Die Netzrichtlinie der Umgebung sperrt `registry.ollama.ai`, `ollama.com` und `huggingface.co`, es konnte kein Modell geladen werden. Der Adapter ist gegen einen simulierten Server und einen echten Ollama-Server ohne Modell geprüft. Ob ein reales Modell das JSON-Schema einhält, wie gut es clustert und wie lange es braucht, ist nicht gemessen. Der Live-Test mit Modell ist vorbereitet (`OLLAMA_TEST_MODEL`) und wurde nicht ausgeführt |
| **Öffentliche TLS-Zertifikate (ACME)** | kein öffentlicher DNS-Name und kein erreichbarer Port 80/443. Geprüft wurde HTTPS nur mit Caddys interner CA (`CADDY_TLS_MODE=internal`) und einem auf 127.0.0.1 abgebildeten Testnamen |
| **Last und Antwortzeiten** | keine Lasttests durchgeführt; Kapazitätsangaben sind Konfigurationsgrenzen, keine Messwerte |
| **Hochverfügbarkeit** | nicht umgesetzt und nicht geprüft (ein Server, jeder Dienst einmal) |
| **GitHub Actions: erster Lauf** | Die Workflow-Datei scheiterte im ersten Lauf im Job `frontend` am Fehler aus Fund 4. Ab Commit `06993cb` sind alle Läufe auf dem Pull Request grün (`backend` mit allen Tests gegen PostgreSQL 18, `frontend`, `compose-config`), zuletzt auf `b7e86ed`. Der Lauf auf diesem Dokumentationscommit selbst wurde nicht abgewartet. Die GitHub-Läufe führen nicht die Docker-Browsertests, den Produktions-Smoke-Test und den Backup-Test aus; diese liefen nur lokal wie oben beschrieben |
| **Browser außer Chromium** | Firefox und WebKit nicht geprüft |
| **Echte Unternehmens-SSO, SCIM, MFA** | nicht umgesetzt |
| **Virenscan, Penetrationstest, Datenschutz- und Rechtsprüfung** | nicht durchgeführt |
| **Wiederherstellung unter Produktionsbedingungen** | Restore wurde mit dem Demo-Stack in einem Testprojekt bewiesen, nicht mit einer Produktionsinstallation und nicht von einem anderen Host |
| **Aktualität der Abhängigkeitsversionen gegen offizielle Quellen** | Versionen entstammen der Auflösung beim Bau; ein Abgleich mit den Veröffentlichungsseiten zum Abschluss ist nicht erfolgt (siehe ADR 0001) |

## Reproduzieren

```sh
export EXTRA_CA_BUNDLE=/pfad/zur/ca.pem   # nur hinter einem TLS-prüfenden Proxy
export CHROMIUM_PATH=/pfad/zu/chromium    # nur wenn Playwright seinen Browser nicht herunterladen kann
scripts/test_integration.sh
scripts/test_e2e.sh
scripts/test_prod_smoke.sh
scripts/test_backup_restore.sh
scripts/check_prod_config.sh
# optional, benötigt Ollama (siehe docs/operations/ollama.md)
OLLAMA_OVERLAY=1 AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b scripts/test_e2e.sh 40-ollama
(cd backend && OLLAMA_TEST_URL=http://127.0.0.1:11434 uv run pytest ../tests/live -v)
```
