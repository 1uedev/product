# ADR 0005: Objektspeicher über S3-Protokoll

Status: angenommen

## Entscheidung
* Dateien liegen in einem S3-kompatiblen Speicher (SeaweedFS), die Anwendung spricht ausschließlich das S3-Protokoll (boto3). Ein Wechsel zu AWS S3, MinIO oder Ähnlichem ändert nur Konfiguration.
* Schlüssel: `tenant/<tenant_id>/<zufälliger Schlüssel>`; ein CHECK in `app.files` bindet den Schlüssel an das Mandantenpräfix. Dateinamen der Nutzer werden nie Teil des Schlüssels und beim Download bereinigt ausgeliefert.
* Upload läuft durch die API (Größenlimit, Typ- und Inhaltsprüfung, SHA-256 über den geprüften Inhalt; die Datei wird dafür im Speicher gehalten, daher das Größenlimit). Erst wenn Objekt und Datenbankzeile zusammenpassen, wird `files.status = 'ready'`. Downloads laufen ebenfalls durch die API nach Berechtigungsprüfung; der Speicher ist nicht öffentlich erreichbar.
* Reihenfolge Schreiben: Datenbankzeile `pending`, Objekt hochladen, Zeile `ready`. Löschen: Zeile `deleting`, Objekt löschen, Zeile entfernen. Ein Absturz dazwischen hinterlässt einen erkennbaren Zustand, den die Wiederherstellung (ADR 0004) aufräumt.
* Backup erfasst Datenbank und Objekte gemeinsam (`scripts/backup.sh`, kurz ruhende Schreiber). Siehe [Backup und Restore](../operations/backup-restore.md).

## Verworfen
* Dateien in der Datenbank (große Sicherungen, Streaming schwierig), vorsignierte öffentliche URLs (umgehen unsere Berechtigungsprüfung und den Mandantenschutz).

## Folgen
Es gibt kein Virenscanning (siehe Grenzen in [Kapazität und Grenzen](../operations/capacity-and-limits.md)). Zulässig sind CSV, TSV, TXT, MD und PDF mit Typ- und Größenprüfung.
