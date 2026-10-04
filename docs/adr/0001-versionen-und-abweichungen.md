# ADR 0001: Versionsstände und Abweichungen von der Vorgabe

Status: angenommen

## Kontext
Die Aufgabe nennt Zielversionen (Python 3.13, PostgreSQL 18, Next.js 16, Node 24 und weitere). Prüfbar ist nur, was in der Build- und Testumgebung tatsächlich lief.

## Entscheidung
Verwendet werden die Versionen, die in `backend/pyproject.toml`, `frontend/package.json` und den Compose-Dateien stehen und mit denen alle Tests liefen. Wo die Vorgabe eine andere Version nennt oder offen lässt, steht hier der Grund.

| Baustein | Eingesetzt (festgelegt in `uv.lock`, `pnpm-lock.yaml`, Compose) | Anmerkung |
|---|---|---|
| Python | 3.13 (`python:3.13.16-slim-trixie`) | wie in der Vorgabe |
| Node.js | 24 (`node:24.21.0-alpine3.24`) im Container | wie in der Vorgabe; lokal genügt Node 22 oder neuer |
| PostgreSQL | 18.6 (`postgres:18.6-alpine3.24`, Digest festgelegt) | RLS, Trigger, generierte Spalten; `pg_trgm` für den Trigramm-Index auf Kundennamen |
| FastAPI / Pydantic | 0.142.2 / 2.13.5 | OpenAPI wird aus dem Code exportiert (`docs/openapi.json`) |
| SQLAlchemy / Alembic / psycopg | 2.1.3 / 1.20.0 / 3.3.6 | synchron, siehe ADR 0008 |
| Celery / RabbitMQ | 5.6.3 / 4.3.6 (Digest festgelegt) | Fernsteuerung des Workers abgeschaltet, weil RabbitMQ 4.3 die dafür nötigen Queues nicht mehr zulässt |
| boto3 / anthropic SDK | 1.43.108 / 1.11.0 | S3-Zugriff / KI-Adapter |
| Keycloak | 26.8.0 (Digest festgelegt) | Demo: `start-dev` mit Realm-Import. Produktion: `start` mit Umgebungsvariablen-Ersetzung |
| SeaweedFS | 4.48 (Digest festgelegt) | S3-kompatibel; Zugriff nur über das S3-Protokoll |
| Caddy | 2.11.6 (Digest festgelegt) | Gateway, HTTPS per ACME oder `tls internal` |
| Next.js / React / TanStack Query | 16.3.8 / 19.3.0 / 5.x | App Router, Daten über die API |
| Playwright / axe | 1.63 / 4.13 | echte Anmeldung gegen Keycloak, Barrierefreiheitsprüfung |

Es wurden keine Vorabversionen und keine ungebundenen `latest`-Tags verwendet. **Nicht erfolgt:** ein erneuter Abgleich der Versionen mit den offiziellen Veröffentlichungsseiten zum Abschluss; die Versionen entstammen der Auflösung beim Bau und haben die Tests bestanden.

## Folgen
Wer Versionen anhebt, führt `scripts/test_integration.sh` und `scripts/test_e2e.sh` erneut aus. Die Versionsnummern in `docs/VERIFICATION.md` nennen den Stand der letzten Läufe.
