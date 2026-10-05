# Grant-Matrix und Datenbankrollen

Rollen werden von `infra/postgres/db-init.sh` angelegt (idempotent, ohne etwas zu löschen), die Rechte vergeben die Migrationen
(`0001`, `0004`). Keine Laufzeitrolle ist Superuser, Tabellenbesitzer oder hat `BYPASSRLS` (geprüft in `tests/integration/test_schema.py`).
Jeder Prozess erhält in Compose nur das Passwort seiner eigenen Rolle.

| Rolle | Prozess | `identity.*` | `app.*` | `infra.outbox` | `infra.*` Funktionen |
|---|---|---|---|---|---|
| `de_migrator` | Migration, Bootstrap, Admin-CLI (Tabellenbesitzer) | Eigentümer | Eigentümer, Policies gelten auch für ihn (FORCE) | Eigentümer | alle |
| `de_auth` | API (Anmeldung, Mitgliedschaften, Einladungen) | SELECT/INSERT/UPDATE/DELETE je Tabelle minimal (tenants nur SELECT) | kein Zugriff | kein Zugriff | kein Zugriff |
| `de_app` | API (Fachlogik) | kein Zugriff | SELECT/INSERT/UPDATE/DELETE unter Mandantenkontext; `audit_events`, `problem_history` nur SELECT/INSERT | INSERT und SELECT eigener Mandant | `current_tenant_id()` |
| `de_worker` | Celery-Worker | kein Zugriff | wie `de_app` | INSERT und SELECT eigener Mandant | `current_tenant_id()`, `find_stale_jobs()`, `find_stale_files()` |
| `de_outbox` | Outbox-Publisher | kein Zugriff | **kein** Zugriff (keine Fachinhalte) | SELECT von Metadatenspalten, UPDATE nur von `published_at`, `attempts`, `last_error_code`, `available_at` (alle Mandanten) | keine |
| `de_recovery` | NOLOGIN, besitzt die beiden SECURITY-DEFINER-Funktionen | kein Zugriff | Spaltenrechte nur auf `jobs(id, tenant_id, kind, status, lease_expires_at, updated_at, attempts)` und `files(id, tenant_id, status, object_key, created_at, updated_at)` | SELECT auf `tenant_id, job_id, published_at` | Eigentümer |
| `keycloak` | Keycloak | eigene Datenbank `keycloak`, getrennt von `decision_evidence` | | | |

## Ausnahmen von RLS (je mit ADR 0002)

1. **`identity.*`**: wird vor der Mandantenwahl gebraucht (Sitzung, Anmeldung, Mitgliedschaftsauflösung). Gekapselt in eigenem Schema, nur `de_auth`, ein einziger
   Repository-Modulpfad. Jede mandantenbezogene Abfrage trägt einen expliziten Mandantenfilter nach geprüfter Autorisierung.
2. **`infra.outbox`**: der Publisher muss mandantenübergreifend Zeilen finden. RLS ist trotzdem aktiv: `de_app`/`de_worker` dürfen nur Zeilen des eigenen
   Mandantenkontexts anlegen und lesen; `de_outbox` sieht per Policy alle Zeilen, aber nur die Metadatenspalten.
3. **Recovery**: verwaiste Jobs und Dateien werden mandantenübergreifend über `SECURITY DEFINER`-Funktionen gefunden, die nur Kennungen
   (`tenant_id`, `job_id`/`file_id`) zurückgeben. Das Aufräumen selbst läuft danach je Mandant unter normalem Kontext.

## Mandantenkontext

`set_config('app.tenant_id', :id, true)` (parametrisiert, transaktionslokal) wird in `db/session.py` bei **jedem** Transaktionsbeginn einer Session gesetzt
(`after_begin`-Hook), auch nach einem Zwischen-Commit. Ohne Kontext liefert `infra.current_tenant_id()` NULL, Policies matchen nicht, nichts ist lesbar
oder schreibbar. Die Tests prüfen Pool-Wiederverwendung, fehlenden Kontext, fremde Kennungen, Suche, Jobs, Downloads, Exporte und KI-Belege.
