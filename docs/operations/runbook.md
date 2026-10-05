# Betriebshandbuch (Fehlersuche)

Befehle gelten für das Demo-Projekt. In Produktion zusätzlich `-f compose.prod.yaml --env-file .env.prod`.

## Zustand prüfen

```sh
docker compose ps                       # alle Dienste sollten healthy sein
curl -fsS http://localhost:8480/api/health/ready   # Datenbank, Migrationsstand, Speicher
docker compose logs --tail=100 api worker outbox-publisher
```

Logs sind JSON mit `request_id` (API) und `correlation_id` (Jobs). Eine Fehlermeldung im Browser nennt die `request_id`; damit lässt sich die Anfrage im Log finden.

## Typische Probleme

| Beobachtung | Ursache | Maßnahme |
|---|---|---|
| Job bleibt „wartet“ | Broker nicht erreichbar oder Publisher gestoppt. Die Aufgabe geht nicht verloren, sie liegt in der Outbox | `docker compose ps rabbitmq outbox-publisher`; nach Rückkehr des Brokers wird automatisch nachgesendet |
| Job bleibt „läuft“ nach Worker-Absturz | Lease noch nicht abgelaufen | die Lease dauert 60 Sekunden und wird vom lebenden Worker laufend verlängert; nach Ablauf übernimmt die Wiederherstellung (beat, alle 30 Sekunden) und stellt neu ein |
| Job „fehlgeschlagen“ | Fehlermeldung steht am Job (`GET /api/v1/workspaces/{id}/jobs/{job}`) und in der Oberfläche; technischer Code im Worker-Log | Ursache beheben, Aktion in der Oberfläche wiederholen (Imports sind idempotent) |
| Anmeldung scheitert mit „Issuer“ | `PUBLIC_ORIGIN` und der Ursprung im Browser stimmen nicht überein | Adresse exakt gleich verwenden (`localhost` ist nicht `127.0.0.1`) |
| Keycloak „Invalid redirect uri“ | Realm wurde mit anderem Ursprung importiert (siehe [Produktion](production.md)) | Redirect-URIs im Client nachziehen |
| 403 bei Schreibaktionen im Browser | CSRF-Header oder Origin fehlen, meist durch Zugriff über einen anderen Host | Seite über den konfigurierten Ursprung öffnen |
| KI-Analyse „Budget erschöpft“ | monatliches Aufrufbudget des Workspace | Wert in den Workspace-Einstellungen (Admin) erhöhen |
| KI-Analyse „Anbieter nicht erreichbar“ | Netzwerk, Schlüssel oder Modell | Log des Workers; die Anwendung wechselt **nicht** auf den Demo-Adapter |
| Upload „Datei zu groß“ | Grenzen im Workspace | Einstellungen oder Datei teilen |
| Datei „wartet“ ohne Abschluss | Abbruch während des Uploads | die Wiederherstellung räumt nach 30 Minuten auf |

## Zustände kontrollieren (Lesezugriff als Betreiber)

```sh
docker compose exec postgres psql -U postgres -d decision_evidence -c "select status, count(*) from app.jobs group by 1"
docker compose exec postgres psql -U postgres -d decision_evidence -c "select count(*) from infra.outbox where published_at is null"
```

Der Superuser umgeht RLS; benutzen Sie ihn nur lesend und bewusst. Datenänderungen von Hand sind nicht vorgesehen.

## Administration per CLI

```sh
docker compose run --rm --no-deps bootstrap python -m decision_evidence.tools.admin list-tenants
docker compose run --rm --no-deps bootstrap python -m decision_evidence.tools.admin set-external-org acme org-123
```

## Demo zurücksetzen

`docker compose down -v` löscht alle Daten des Projekts (Datenbanken, Dateien, Broker) und baut beim nächsten Start den Demo-Datenbestand neu auf. Nur in Demo- und Testumgebungen verwenden.
