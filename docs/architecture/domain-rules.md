# Fachliche Regeln und ihre Umsetzung

Die Regeln sind als reine Funktionen ohne Datenbankzugriff umgesetzt (`modules/metrics/domain.py`, `modules/scoring/domain.py`) und mit Unit-Tests belegt
(`tests/unit/test_metrics.py`, `test_scoring.py`). Rechnen geschieht ausschließlich mit `Decimal`.

| Regel | Umsetzung |
|---|---|
| Häufigkeit zählt eindeutige Kunden, Aussagen getrennt | `supporting_customers` (Menge der Kunden) und `supporting_statements` werden immer beide gezeigt. Aussagen ohne Kunde zählen nicht als Kunden (`unassigned_statements`) |
| Währungen nie mischen | alle Beträge sind `MoneyBlock`: Summen je Währung, nie eine Gesamtsumme. Bei mehreren Währungen erscheint die Warnung `mixed_currencies` |
| Kennzahlenarten nie mischen | ARR, Jahresumsatz, offene Pipeline und verlorenes Volumen sind getrennte Blöcke |
| Unbekannt ist nicht 0 | fehlende Werte bleiben NULL (auch per CHECK in der Datenbank), werden als `unknown_count` gezählt und mit der Warnung `unknown_values` gezeigt. Die Policy-Regel `exclude/zero/block` entscheidet nur beim Scoring, nie bei der Anzeige |
| Kundenwert nur einmal | `portfolio_totals` bildet die Vereinigungsmenge der Kunden über Probleme; ein Kunde mit mehreren Problemen geht mit seinem vollen Wert genau einmal ein. Die naive Summe wird zum Vergleich ausgewiesen |
| Zuordnung von Pipeline | `pipeline_attributed`: nur offene Chancen, die ein unterstützender Beleg ausdrücklich nennt. `pipeline_customer_level`: alle offenen Chancen der betroffenen Kunden als Kontext, klar als nicht zugeordnet gekennzeichnet |
| Alter und Abdeckung der Belege | Alter (älteste, neueste, Median), Anteil frischer Belege (Standard 180 Tage, je Workspace einstellbar), Anteil betroffener Kunden an allen Kunden, Anteil Kunden mit bekanntem Wert, Anteil menschlich verifizierter Belege |
| Laute Minderheit | Warnungen `single_customer_dominates` (mindestens die Hälfte der Aussagen von einem Kunden, ab drei Aussagen), `minority_signal` (weniger als 25 % der Kunden), `repeated_customers` |
| Gegenbelege | `contradicts` wird gezählt, `conflicted_customers` nennt Kunden mit zustimmenden und widersprechenden Aussagen; Warnung `has_contradictions` |
| Scoring transparent und deterministisch | sieben Kriterien mit Gewichten (Summe 1), Formel `score = 100 * Σ w_i * v_i` über bekannte Werte; jedes Teilergebnis, Gewicht, effektives Gewicht und Fehlwert wird ausgegeben. Formelversion `v1`. Kein Zufall, keine KI |
| Fehlende Werte beim Scoring | Policy `exclude` (Gewichte der bekannten Kriterien werden renormiert, Abdeckung wird ausgewiesen), `zero` (bewusst schlechter) oder `block` (kein Ergebnis, bis die Lücke geschlossen ist) |
| Sensitivität | jedes Gewicht wird um ±25 % verändert (renormiert), die Rangfolge neu berechnet; die Oberfläche zeigt, ob die Reihenfolge stabil ist oder welche Annahme sie kippt |
| Aufwand und Risiko sind menschliche Eingaben | Aufwandsspanne (low/high, Einheit) und Risiko kommen vom Team. Die KI schätzt sie nie. Fehlt der Aufwand, ist das Kriterium unbekannt |
| Entscheidungsdokument | Snapshot von Belegen (mit wörtlichen Zitaten), Kennzahlen und Scoring zum Zeitpunkt der Einreichung. Zustände draft, in_review, approved, superseded. Freigabe nach Vier-Augen-Prinzip (Einreichende dürfen nicht selbst freigeben, außer der Workspace erlaubt es). Freigegebene Revisionen sind per Datenbanktrigger unveränderlich; Änderungen erzeugen eine neue Revision |
| Drift | Beim Öffnen einer Revision vergleicht die Anwendung Snapshot und aktuelle Daten (Kunden, Belege, Policy) und zeigt Abweichungen, ohne den Snapshot zu verändern |
| Datenschutz im Export | CSV-Zellen, die mit `=`, `+`, `-`, `@`, Tab oder CR beginnen, werden mit einem Präfix entschärft (Formelinjektion). Der Export enthält Zitate und Quellenverweise, aber nur Daten des eigenen Mandanten |
| Nebenläufigkeit | Probleme, Initiativen und Entscheidungsdokumente tragen eine `version`; schreibende Anfragen senden `If-Match` und erhalten bei Konflikt 412 beziehungsweise 409 mit dem aktuellen Stand |
