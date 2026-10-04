# Kapazität, Grenzen und bekannte Einschränkungen

## Ausgelegt für

Ein Server, wenige Organisationen, je Organisation einige tausend Kunden-Aussagen und wenige Nutzer gleichzeitig. Es wurde **keine Lastmessung** durchgeführt. Die folgenden Werte sind Konfigurationsgrenzen, keine gemessene Leistungsfähigkeit.

| Grenze | Standard | Wo änderbar |
|---|---|---|
| Dateigröße pro Upload | 10 MB (max. 100 MB) | Workspace-Einstellungen (Admin) |
| Zeilen pro Import | 5000 (max. 100000) | Workspace-Einstellungen |
| gleichzeitig laufende Jobs je Workspace | 3 | Workspace-Einstellungen |
| KI-Aufrufe pro Monat und Workspace | 50 | Workspace-Einstellungen |
| Chunks im KI-Kontext | 400 | Workspace-Einstellungen |
| Aktualität von Belegen | 180 Tage | Workspace-Einstellungen |
| Datenbankverbindungen je Prozess | 8 (+8 Überlauf) | `DB_POOL_SIZE` |
| Worker-Parallelität | 2 | Befehlszeile in Compose (`--concurrency`) |

Hochgeladene Dateien werden für Prüfung und SHA-256 im Speicher gehalten; mit dem Maximalwert von 100 MB belegt ein Upload entsprechend Arbeitsspeicher in der API.

## Nicht umgesetzt oder nicht geprüft

* **Keine Hochverfügbarkeit, kein Failover, keine Replikation** (siehe [Produktion](production.md)).
* **Kein Virenscan** hochgeladener Dateien; es wird nur nach Typ, Endung und Größe geprüft.
* **Keine Lasttests, keine Messung von Antwortzeiten** unter Last. Die gezeigte Demo-Größe (je Demo-Workspace rund 18 Kunden, 36 Chancen, über 100 Aussagen) ist klein.
* **KI mit echtem Anbieter nicht im Test ausgeführt.** Der Anthropic-Adapter ist gegen einen Stub des SDK geprüft (Anfrageform, Fehlerabbildung, Ablehnung), nicht gegen die echte API. Die Qualität der Cluster und Entwurfstexte mit einem echten Modell ist unbekannt.
* **Demo-KI** ist ein lexikalisches Regelwerk und belegt nur, dass der Ablauf funktioniert, nicht dass ein Sprachmodell gute Ergebnisse liefert.
* **Keine Login-Drosselung in der Anwendung**; Brute-Force-Schutz gegen Passwörter liefert Keycloak (aktiv in beiden Realms). Keine Ratenbegrenzung der API.
* **Keine unternehmensspezifische SSO** (SAML, SCIM, Gruppenmapping), keine MFA-Konfiguration im Realm (kann in Keycloak ergänzt werden).
* **Keine E-Mail-Zustellung**: Einladungen erzeugen einen Link, den Owner weitergeben. Kein Passwort-Reset in der Anwendung (liegt bei Keycloak).
* **Nur deutsche Oberfläche**, Zeitzone und Locale je Workspace sind gespeichert, die Oberfläche ist aber nicht mehrsprachig.
* **Kein Kommentar-, Benachrichtigungs- oder Aufgabensystem** über Entscheidungskommentare hinaus.
* **Keine Datenaufbewahrungs- und Löschfristen**: Löschen einzelner Quellen ist möglich (mit Prüfung der Abhängigkeiten), ein Löschen ganzer Workspaces ist nur über den Betreiber möglich.
* **CI**: `.github/workflows/ci.yml` wurde in dieser Umgebung nie ausgeführt; die dort aufgerufenen Skripte wurden lokal ausgeführt.
