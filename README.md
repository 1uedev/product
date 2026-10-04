# decision-evidence

Werkzeug für kleine B2B-Produktteams, das Kundenfeedback und wirtschaftlichen Kontext in überprüfbare Entscheidungsvorlagen übersetzt.
Die KI schlägt Problemcluster vor und entwirft Texte. Entscheiden und freigeben tun Menschen, jeder Beleg führt zur Originalstelle.

Ablauf: **Import** (Kunden, Verkaufschancen, Feedback, Gesprächsnotizen) → **Analyse** → **Beleg öffnen** → **Cluster korrigieren** → **Initiative** → **Entscheidungsentwurf** → **Freigabe** → **Export**.

> Der Stand der Umsetzung steht in [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md), die tatsächlich ausgeführten Prüfungen in [docs/VERIFICATION.md](docs/VERIFICATION.md). Beides bitte vor einer Bewertung lesen.

## Schnellstart (Demo, lokal)

Benötigt werden nur Docker mit Compose v2 und etwa 6 GB freier Arbeitsspeicher. Node und Python müssen nicht installiert sein.

```sh
docker compose up --build -d
docker compose ps          # warten, bis alle Dienste healthy sind (erster Start einige Minuten)
```

Dann im Browser **http://localhost:8480** öffnen. Der Stack veröffentlicht nur das Gateway auf `127.0.0.1`. Datenbank, Broker, Speicher und Keycloak sind von außen nicht erreichbar.
Port und Projektname lassen sich ändern, damit mehrere Werkzeuge nebeneinander laufen: `APP_PORT=8490 COMPOSE_PROJECT_NAME=meinprojekt docker compose up --build -d`
(den Ursprung `http://localhost:<port>` im Browser exakt so verwenden).

Hinter einem TLS-prüfenden Proxy scheitern Image-Builds an Zertifikaten. Dann `EXTRA_CA_BUNDLE=/pfad/zu/ca.pem` setzen (siehe `.env.example`).

### Demo-Anmeldung

Nur im erkennbaren Demomodus (Banner „Demo-Modus“ oben). Passwort für alle Nutzer: `demo-Passw0rd!`

| Nutzer | Workspace | Rolle |
|---|---|---|
| `alice@lumen.example` | Lumen Analytics GmbH (Demo) | Owner |
| `dora@lumen.example` | Lumen Analytics GmbH (Demo) | Admin |
| `bob@lumen.example` | Lumen Analytics GmbH (Demo) | Editor |
| `vera@lumen.example` | Lumen Analytics GmbH (Demo) | Viewer |
| `finn@fjord.example` | Fjord Systems AG (Demo) | Owner (zweiter, isolierter Mandant) |

Der Demo-Workspace „Lumen“ enthält 18 Kunden, 36 Verkaufschancen, über 100 Feedbackaussagen (mit Dubletten, Gegenbelegen, fehlendem Umsatz und den Währungen EUR und CHF), sechs Probleme und drei Initiativen.
Der Seed ist idempotent und wird bei jedem Start geprüft. Die Beispieldateien für einen eigenen Import liegen in [docs/examples](docs/examples/README.md).

Probieren Sie den Hauptablauf: als `bob` anmelden, im Workspace „Import“ die Dateien aus `docs/examples/` in der Reihenfolge Kunden, Chancen, Feedback hochladen, „Analyse starten“, ein Problem öffnen, einen Beleg ansehen, Cluster zusammenführen oder aufteilen,
Initiative anlegen, Entscheidungsentwurf erzeugen. Freigeben muss danach eine zweite Person (`alice` oder `dora`).

## Adressen (Demo)

| URL | Zweck |
|---|---|
| http://localhost:8480/ | Oberfläche |
| http://localhost:8480/api/docs | interaktive API-Dokumentation (nur Demo und Test) |
| http://localhost:8480/api/health/live, `/api/health/ready` | Liveness und Readiness |
| http://localhost:8480/auth/ | Keycloak (Anmeldung; Admin-Konsole ist über das Gateway gesperrt) |

## Auf den echten KI-Anbieter umstellen

Standard ist `AI_PROVIDER=mock`, eine deterministische **Demo-KI** (kein Sprachmodell, ausdrücklich so gekennzeichnet). Für Claude:

```sh
AI_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... ANTHROPIC_MODEL=<Modellname> docker compose up -d
```

* Der Schlüssel bleibt serverseitig (API und Worker). Der Browser sieht ihn nie.
* Es gibt keinen stillen Rückfall auf die Demo-KI: Fehler des Anbieters erscheinen als fehlgeschlagener Job mit Meldung.
* Das Modell muss strukturierte Ausgaben unterstützen. Welche Daten übertragen werden, steht in [docs/architecture/ai-dataflow.md](docs/architecture/ai-dataflow.md).
* **Mit der echten API wurde in dieser Arbeit nichts getestet** (kein Schlüssel). Siehe [docs/VERIFICATION.md](docs/VERIFICATION.md).

### Lokales Modell mit Ollama

Wenn keine Daten an einen gehosteten Dienst gehen sollen, läuft die Analyse auch mit einem Modell auf der eigenen Hardware:

```sh
# Ollama läuft auf dem Docker-Host (muss aus Containern erreichbar sein) ...
AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b docker compose up --build -d
# ... oder als zusätzlicher Container
AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b docker compose -f compose.yaml -f compose.ollama.yaml up --build -d
docker compose -f compose.yaml -f compose.ollama.yaml exec ollama ollama pull qwen3:8b
```

Ein fehlendes Modell, ein zu großer Kontext oder ein nicht erreichbarer Server enden als sichtbarer Jobfehler, nie als stiller Wechsel auf die Demo-KI. Alle Einstellungen, Fehlerbilder und Grenzen:
[docs/operations/ollama.md](docs/operations/ollama.md). **Eine echte Textgenerierung mit einem Modell wurde hier nicht getestet** (der Modell-Download war in der Entwicklungsumgebung gesperrt).

## Produktion (ein Server)

`compose.prod.yaml` verlangt alle Geheimnisse, startet Keycloak im Produktionsmodus ohne Demo-Nutzer, terminiert HTTPS im Gateway und verweigert Demo-Werte.
Schritt für Schritt: [docs/operations/production.md](docs/operations/production.md). Das ist **Betriebsbereitschaft für einen Server, weder Hochverfügbarkeit noch Rechtskonformität**.

## Migrationen

Schema, Constraints, RLS-Policies und Rechte entstehen ausschließlich durch Alembic-Migrationen (`backend/migrations`), ausgeführt vom einmaligen Dienst `migrate` mit der Rolle `de_migrator`, nie von den API-Prozessen.
Rollen legt der einmalige Dienst `db-init` an (idempotent, löscht nichts).

```sh
docker compose run --rm --no-deps migrate               # erneut anwenden (idempotent)
docker compose run --rm --no-deps migrate alembic current
```

Das Datenmodell mit ER-Diagramm: [docs/architecture/data-model.md](docs/architecture/data-model.md).

## Tests

Alle Testläufe verwenden isolierte Compose-Projekte mit dem Präfix `de-test-` und eigenen Volumes. Kein Skript löscht Daten außerhalb dieser Projekte.

| Prüfung | Befehl |
|---|---|
| Backend: Lint, Typen | `cd backend && uv run ruff check src ../tests && uv run mypy src` |
| Backend: Unit- und Integrationstests (echtes PostgreSQL 18, eingeschränkte Rollen, RLS) | `scripts/test_integration.sh` |
| Frontend: Lint, Typen, Build | `cd frontend && pnpm install && pnpm lint && pnpm typecheck && pnpm build` |
| Browser-Tests (echte Keycloak-Anmeldung, kompletter Stack, inkl. Barrierefreiheit und Ausfälle) | `scripts/test_e2e.sh` |
| Nur der Hauptablauf | `scripts/test_e2e.sh 10-main-flow` |
| Backup und Restore in ein neues Projekt | `scripts/test_backup_restore.sh` |
| Produktionskonfiguration (ohne Container) | `scripts/check_prod_config.sh` |
| Lokales Modell: Fehlerbilder gegen einen echten Ollama-Server | `OLLAMA_TEST_URL=http://127.0.0.1:11434 uv run pytest ../tests/live` (im Ordner `backend`) |
| Produktionsmodus: HTTPS, erster Nutzer, Einladung, sicheres Cookie (isoliertes Projekt, interne CA) | `scripts/test_prod_smoke.sh` |

Lokale Entwicklung braucht Python 3.13 mit `uv` und Node 22 oder neuer mit `pnpm` (die Container nutzen Node 24). Ergebnisse der letzten Läufe: [docs/VERIFICATION.md](docs/VERIFICATION.md).

## Backup und Restore

```sh
scripts/backup.sh --project <projekt> [--compose-file compose.prod.yaml --env-file .env.prod] --out <verzeichnis>
scripts/restore.sh --from <sicherung> --project <neues-projekt> [--compose-file ... --env-file ...]
```

Restore schreibt nur in ein neues, leeres Projekt und löscht nie etwas. Details und Grenzen: [docs/operations/backup-restore.md](docs/operations/backup-restore.md).

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) | was umgesetzt ist, was offen ist (Fortsetzung nach Kontextwechsel möglich) |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | ausgeführte Prüfungen mit Befehl und Ergebnis, nicht ausgeführte Prüfungen |
| [docs/architecture/overview.md](docs/architecture/overview.md) | Bausteine, Prozesse, Hauptablauf |
| [docs/architecture/data-model.md](docs/architecture/data-model.md) | Tabellen und Mermaid-ER-Diagramm |
| [docs/architecture/grant-matrix.md](docs/architecture/grant-matrix.md) | Datenbankrollen, Rechte, RLS-Ausnahmen |
| [docs/architecture/ai-dataflow.md](docs/architecture/ai-dataflow.md) | Datenfluss zur KI, Prüfung der Belege |
| [docs/architecture/domain-rules.md](docs/architecture/domain-rules.md) | fachliche Regeln und ihre Umsetzung |
| [docs/architecture/suite-compatibility.md](docs/architecture/suite-compatibility.md) | Vorbereitung für eine spätere Produktsuite |
| [docs/adr](docs/adr) | Entscheidungen: Versionen, Mandantentrennung, Auth, Jobs, Speicher, KI, Suche, Python-Stack |
| [docs/operations](docs/operations) | Produktion, Betriebshandbuch, Backup/Restore, lokales Modell (Ollama), Kapazität und Grenzen |
| [docs/openapi.json](docs/openapi.json) | OpenAPI (aus dem Code exportiert) |
| [docs/examples](docs/examples/README.md) | Beispieldateien und Importregeln |

## Bekannte Grenzen (Kurzfassung)

* Eine Instanz auf einem Server. Keine Hochverfügbarkeit, keine Lastmessung, kein Virenscan der Uploads.
* Echter KI-Anbieter und echte lokale Modelle (Ollama) nicht mit Textgenerierung getestet, Demo-KI ist kein Qualitätsnachweis.
* Keine Anbindung an CRM-, Ticket- oder andere Systeme; keine E-Mail-Zustellung (Einladungen sind Links).
* Keine Rechtsberatung: Datenschutz- und Aufbewahrungspflichten bleiben beim Betreiber.

Vollständig: [docs/operations/capacity-and-limits.md](docs/operations/capacity-and-limits.md).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
