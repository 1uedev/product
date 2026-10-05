# Backup und Restore

Was gesichert wird: die Anwendungsdatenbank, die Keycloak-Datenbank und alle Objekte im Dateispeicher eines Compose-Projekts. Zusammengehörigkeit: Das Skript hält API, Worker und Publisher kurz an,
damit Datenbank und Objekte denselben Stand abbilden, und startet sie danach wieder (auch bei Fehlern).

## Sichern

```sh
scripts/backup.sh --project decision-evidence-prod --compose-file compose.prod.yaml --env-file .env.prod --out /srv/backups
```

* Das Skript ändert keine Daten, schreibt nur in ein neues Verzeichnis und überschreibt nie ein vorhandenes.
* Das Ergebnis enthält `decision_evidence.dump` und `keycloak.dump` (pg_dump, Custom-Format), die Objekte samt `objects-manifest.json` (SHA-256 je Objekt), `SHA256SUMS` und `manifest.json` (Zeitpunkt, Ursprungsprojekt, Schemarevision). Das Verzeichnis ist nur für den Besitzer lesbar.
* Wer Alltagsrisiken abdecken will, kopiert das Verzeichnis auf ein anderes System. **Die Sicherung enthält Kundendaten und ist entsprechend zu schützen.** Geheimnisse (`.env.prod`) sind nicht Teil der Sicherung und müssen separat aufbewahrt werden; ohne `SECRET_KEY` sind verschlüsselte ID-Token in Sitzungen nicht lesbar (Nutzer melden sich neu an, Daten gehen nicht verloren).

## Wiederherstellen

```sh
scripts/restore.sh --from /srv/backups/<verzeichnis> --project decision-evidence-restored --compose-file compose.prod.yaml --env-file .env.prod
```

* Ziel ist immer ein **neues, leeres** Projekt. Das Skript weigert sich, in ein Projekt mit vorhandenen Volumes oder in das Ursprungsprojekt zu schreiben, und löscht nie etwas.
* Es prüft die Prüfsummen, spielt beide Datenbanken ein, lädt die Objekte hoch (nach erneuter Prüfsummenkontrolle), startet den Stack und gleicht danach die Dateieinträge der Datenbank mit den vorhandenen Objekten ab (`storage_backup verify`).
* Einen beschädigten Betrieb ersetzen: alten Stack stoppen (`docker compose -p <alt> down`, ohne `-v`), Volumes von Hand umbenennen oder sichern, dann in den freigewordenen Projektnamen wiederherstellen (`--allow-source-project-name`).

## Nachweis

`scripts/test_backup_restore.sh` führt den gesamten Weg in isolierten Testprojekten aus (befüllter Stack A, Sicherung, A gestoppt, Wiederherstellung in B, Tabellenzählungen im Vergleich, Objektabgleich, echter Browser-Login in B). Das Ergebnis des letzten Laufs steht in [VERIFICATION.md](../VERIFICATION.md).

## Grenzen

Kein kontinuierliches Archivieren (Point-in-Time-Recovery), kein automatischer Zeitplan (z. B. per Cron selbst einrichten), keine Verschlüsselung der Sicherung durch das Skript (Zielspeicher verschlüsseln oder Ausgabe mit `age`/`gpg` nachbehandeln). Eine Sicherung gilt erst als brauchbar, wenn ein Wiederherstellungstest in Ihrer Umgebung gelaufen ist.
