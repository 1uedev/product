# Produktivbetrieb auf einem Server

`compose.prod.yaml` beschreibt einen Betrieb auf **einem** Server. Das ist Betriebsbereitschaft für eine kleine Installation, keine Hochverfügbarkeit und keine Rechtskonformität
(siehe Abschnitt „Was dies nicht ist“).

## Unterschiede zum Demo-Stack

| | Demo (`compose.yaml`) | Produktion (`compose.prod.yaml`) |
|---|---|---|
| Geheimnisse | bekannte Demo-Werte als Standard | alle Pflicht, ohne Standardwert; Compose bricht ohne sie ab |
| Anwendung | `APP_ENV=demo` | `APP_ENV=production`: die Anwendung verweigert den Start bei Demo-Markern in Geheimnissen, bei `AI_PROVIDER=mock` (API und Worker), bei einem Ursprung ohne HTTPS und bei einem Issuer auf localhost |
| Daten | Seed mit zwei Demo-Workspaces | nie Seed, nur optional der erste leere Workspace |
| Keycloak | `start-dev`, Realm mit Demo-Nutzern, HTTP | `start`, Realm ohne Nutzer, `sslRequired=external`, Brute-Force-Schutz, Admin-Konsole nicht über das Gateway erreichbar |
| Gateway | HTTP auf 127.0.0.1 | HTTPS auf 80/443 mit automatischem Zertifikat (ACME) |
| Images | `dev`-Tag | Tag aus `DE_VERSION`; Basis-Images sind auf Digests festgelegt |

## Voraussetzungen

* Linux-Server mit Docker Compose v2, DNS-Name, der auf den Server zeigt, offene Ports 80 und 443 (für die automatische Zertifikatsausstellung).
* Ein Anthropic-API-Schlüssel und ein Modellname, der strukturierte Ausgabe unterstützt, sofern KI-Analysen genutzt werden sollen. Ohne Schlüssel startet der Stack nicht, weil der Demo-Adapter in Produktion verboten ist.

## Einrichten

1. `cp .env.example .env.prod` und alle Werte im Abschnitt „production“ setzen. Passwörter mit `openssl rand -base64 24 | tr '+/' '-_' | tr -d '='` erzeugen, `SECRET_KEY` als Fernet-Schlüssel.
   Die Datei gehört nicht ins Repository und sollte nur für den Betriebsnutzer lesbar sein (`chmod 600`).
2. Konfiguration ohne Start prüfen: `docker compose -f compose.prod.yaml --env-file .env.prod config --quiet` (meldet fehlende Pflichtwerte). `scripts/check_prod_config.sh` prüft ohne Container, dass eine vollständige Umgebung akzeptiert und eine unvollständige abgelehnt wird.
3. Starten: `docker compose -f compose.prod.yaml --env-file .env.prod up -d --build`. Reihenfolge: PostgreSQL, `db-init` (Rollen), `migrate`, Keycloak, Speicher, Broker, `bootstrap`, API, Worker, Publisher, Frontend, Gateway.
4. Prüfen: `curl -fsS https://<host>/api/health/ready` liefert `ready`.

## Erster Zugang

Die Produktions-Realm enthält **keine** Nutzer und keine bekannten Passwörter. Die Keycloak-Verwaltung ist bewusst nicht über das Gateway erreichbar. Der erste Nutzer wird daher innerhalb des Servers angelegt:

```sh
KC="docker compose -p <projekt> -f compose.prod.yaml --env-file .env.prod exec keycloak /opt/keycloak/bin/kcadm.sh"
$KC config credentials --server http://localhost:8080/auth --realm master --user admin --password '<KEYCLOAK_ADMIN_PASSWORD>'
$KC create users -r decision-evidence -s username=owner@example.org -s email=owner@example.org -s emailVerified=true -s enabled=true
$KC set-password -r decision-evidence --username owner@example.org --new-password '<Startpasswort>' --temporary
```

Danach den ersten Workspace anlegen. Entweder beim Start über die Umgebung (`BOOTSTRAP_TENANT_SLUG`, `BOOTSTRAP_TENANT_NAME`, `BOOTSTRAP_OWNER_EMAIL`; der einmalige Einladungslink steht im Log von `bootstrap`:
`docker compose ... logs bootstrap | grep invitation`) oder später per CLI:

```sh
docker compose -f compose.prod.yaml --env-file .env.prod run --rm --no-deps bootstrap python -m decision_evidence.tools.admin create-tenant acme "Acme GmbH" owner@example.org
```

Der Einladungslink bindet an die E-Mail-Adresse; er gilt einmal und läuft ab. Weitere Mitglieder lädt der Owner in der Anwendung ein; sie brauchen ebenfalls einen Keycloak-Nutzer
(oder Sie binden Keycloak an ein bestehendes Verzeichnis an, das wird hier nicht beschrieben).

## Wichtige Hinweise

* **Realm-Import nur beim ersten Start.** Keycloak importiert `realm-prod.json` ausschließlich, wenn die Realm noch nicht existiert. Spätere Änderungen an der Datei (Redirect-URIs, Client-Secret) wirken **nicht**
  und müssen über `kcadm.sh update` oder die Verwaltungsoberfläche (nur intern erreichbar) nachgezogen werden. Ein Wechsel von `PUBLIC_HOST` ist deshalb ein Eingriff in die Realm.
* **Client-Secret** (`OIDC_CLIENT_SECRET`) und Datenbankpasswörter ändern: Wert in `.env.prod` anpassen, Passwort in der Datenbank (`ALTER ROLE`) beziehungsweise in Keycloak angleichen, Dienste neu starten.
  `db-init` setzt Rollenpasswörter bei jedem Lauf auf die konfigurierten Werte, ändert aber nie etwas an Daten.
* **KI**: Aufrufe gehen ausschließlich vom Worker/API zum Anbieter. Welche Daten das sind, steht in [KI-Datenfluss](../architecture/ai-dataflow.md). Das Monatsbudget je Workspace begrenzt die Zahl der Aufrufe.
* **Updates**: neuen Stand holen, `DE_VERSION` erhöhen, `up -d --build`. Migrationen laufen im Dienst `migrate` vor API und Worker. Vorher sichern ([Backup und Restore](backup-restore.md)). Migrationen sind nur vorwärts gerichtet;
  Zurückrollen bedeutet Wiederherstellung der Sicherung.
* **Neuaufbau** (`down -v`) löscht alle Daten. Das Skript `restore.sh` und die Skripte der Tests tun das nie in Projekten ohne `de-test-`-Präfix.

## Was dies nicht ist

* **Keine Hochverfügbarkeit**: ein Server, jeder Dienst einmal. Fällt der Server aus, steht der Dienst bis zur Wiederherstellung. PostgreSQL, Broker und Speicher werden nicht repliziert.
* **Keine Rechtskonformität**: Die Software unterstützt technische Maßnahmen (Mandantentrennung, Audit-Log, Löschbarkeit von Quellen, Export), ersetzt aber keine Datenschutzfolgenabschätzung,
  keinen Auftragsverarbeitungsvertrag mit dem KI-Anbieter, keine Aufbewahrungsregeln und keinen Löschprozess für Betroffenenanfragen. Das ist Sache des Betreibers.
* **Nicht gehärtet gegen**: DDoS, kompromittierten Docker-Host, bösartige Dateien (kein Virenscan), Betreiber mit Datenbankzugriff als Superuser.
