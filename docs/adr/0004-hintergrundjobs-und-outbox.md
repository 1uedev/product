# ADR 0004: Hintergrundjobs mit transaktionaler Outbox

Status: angenommen

## Entscheidung
* Der Job (`app.jobs`) und seine Outbox-Zeile (`infra.outbox`) entstehen in **derselben** Transaktion wie die fachliche Änderung. Ein Absturz zwischen Speichern und Senden verliert keine Aufgabe.
* Ein eigener Prozess (`outbox-publisher`, Rolle `de_outbox` ohne Zugriff auf Fachinhalte) sendet fällige Zeilen mit Publisher Confirms an RabbitMQ und setzt erst danach `published_at`. Zustellung ist mindestens einmal; doppelte Nachrichten sind unschädlich.
* Die Nachricht ist ein versionierter Umschlag mit `event_id`, `type`, `schema_version`, `tenant_id`, `job_id`, `occurred_at`, `correlation_id`. Sie enthält keine Fachinhalte.
* Der Worker beansprucht einen Job mit kurzer Transaktion (Lease, `claim_token`, Versuchszähler), setzt den Mandantenkontext, arbeitet **ohne offene Transaktion** während langer Schritte (KI-Aufruf, Objektspeicher) und schreibt das Ergebnis samt Statusübergang atomar mit Prüfung des Claim-Tokens. Ein verdrängter Worker kann nichts mehr überschreiben.
* Idempotenz: `UNIQUE (tenant_id, kind, idempotency_key)`; Handler sind wiederholbar (z. B. Importe über Inhalts-Hashes, Vorschläge über `created_by_job_id + job_ordinal`).
* Fehlerbehandlung: Wiederholung mit Backoff bis `max_attempts`, danach `failed` mit nutzerlesbarer Meldung und technischem Code. Nutzer sehen Status, Fortschritt und Fehler über `GET /api/jobs/{id}`.
* Wiederherstellung (Celery beat, alle 30 Sekunden): Jobs mit abgelaufener Lease werden erneut eingestellt, nicht veröffentlichte Jobs erneut angekündigt, verwaiste Dateien aufgeräumt. Mandantenübergreifendes Finden geschieht über die Funktionen `infra.find_stale_*` ohne Lesezugriff auf Fachdaten.
* Healthchecks von Worker und Publisher prüfen Heartbeat-Dateien statt Broker-Fernsteuerung.

## Verworfen
* Direktes `delay()` nach dem Commit (Verlust bei Absturz) und Nachrichten mit Fachdaten (Mandanten- und Datenschutzrisiko, veraltete Inhalte).

## Folgen
Ein Brokerausfall führt nur zu Verzögerung: Die Outbox füllt sich und wird nach der Rückkehr des Brokers abgearbeitet (Test `90-resilience.spec.ts`). Auch ein hart beendeter Worker verliert nichts (Lease-Ablauf, erneute Zustellung; ebenfalls getestet).
