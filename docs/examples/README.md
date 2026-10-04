# Importbeispiele

Alle Dateien sind synthetisch (erfundene Firmen, generierte Texte) und stammen aus `backend/src/decision_evidence/tools/demo_data.py`.
Sie liegen identisch unter `tests/fixtures/` und werden von den Browser-Tests verwendet.

| Datei | Art im Importassistenten | Zweck |
|---|---|---|
| `kunden.csv` | Kundenstammdaten | 8 Kunden, zwei Währungen (EUR, CHF), ARR und Jahresumsatz getrennt, Spalten wie `external_id,name,segment,country,commercial_value,value_basis,currency,value_as_of` |
| `opportunities.csv` | Verkaufschancen | 16 Chancen, Phasen `open/won/lost/no_decision`, teils ohne Betrag (bleibt unbekannt) |
| `feedback.csv` | Kundenfeedback | 19 verschiedene Aussagen plus 2 absichtliche Dubletten (gleicher Kunde, gleicher Text, anderer Tag); Verweise per `customer_external_id` und `opportunity_external_id` |
| `gespraechsnotiz.txt` | Dokument / Text | Absätze werden einzeln als Fundstellen (`Absatz n`) erfasst |
| `kunden-fehlerhaft.csv` | Kundenstammdaten | Windows-1252, Semikolon, deutsche Zahlenformate; Zeile 2 hat Ländercode `DEU` und Betrag `abc`: die Vorschau zeigt Zeilenfehler, der Import wird blockiert |

## Reihenfolge

1. Kundenstammdaten, 2. Verkaufschancen, 3. Feedback. Verweise auf Kunden und Opportunities werden gegen bereits importierte Daten geprüft.

## Regeln

* **Zuordnung:** Spalten werden anhand von Namen vorgeschlagen (deutsch und englisch, z. B. `Kundennummer`, `Firma`, `Umsatz`) und lassen sich im Assistenten ändern.
* **Zahlen:** `1.234,56`, `1,234.56` und `1234.56` werden erkannt. Ein Betrag braucht Währung (ISO-Code) und bei Kunden eine Wertbasis (`arr` oder `annual_sales`). Ein leeres Feld bleibt unbekannt und wird nie als 0 gespeichert.
* **Datum:** `2026-09-30`, `30.09.2026`, mit oder ohne Uhrzeit.
* **Dubletten:** Kunden und Opportunities anhand der externen ID (wahlweise überspringen oder aktualisieren). Feedback anhand eines Hashes aus Kunde und normalisiertem Text, zusätzlich anhand der Ticket-ID.
* **Kein stiller Teilimport:** Enthält die Datei Fehler, wird nichts importiert. Der Commit läuft als Hintergrundaufgabe in einer einzigen Transaktion.
* **Limits:** Dateigröße und Zeilenzahl pro Workspace einstellbar (Standard 10 MB, 5000 Zeilen).
