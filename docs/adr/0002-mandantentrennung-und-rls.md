# ADR 0002: Mandantentrennung mit Row Level Security

Status: angenommen

## Kontext
Mehrere Kundenorganisationen teilen sich eine Instanz. Ein Fehler in der Anwendungslogik darf nie Daten eines anderen Mandanten sichtbar machen.

## Entscheidung
1. Ein gemeinsames Schema `app`, jede Tabelle mit `tenant_id NOT NULL`, `UNIQUE (tenant_id, id)` und zusammengesetzten Fremdschlüsseln `(tenant_id, x_id)`. Eine Referenz über Mandantengrenzen scheitert in der Datenbank.
2. `ENABLE` und `FORCE ROW LEVEL SECURITY` auf allen `app`-Tabellen. Policies mit `USING` und `WITH CHECK` auf `tenant_id = infra.current_tenant_id()`.
3. Der Mandantenkontext wird mit `set_config('app.tenant_id', :id, true)` transaktionslokal gesetzt, parametrisiert und bei jedem Transaktionsbeginn erneut (`after_begin`). Ohne Kontext ist nichts lesbar oder schreibbar.
4. Laufzeitrollen sind weder Eigentümer noch Superuser noch `BYPASSRLS` (siehe [Grant-Matrix](../architecture/grant-matrix.md)).
5. Der Mandant wird nie aus Hostnamen oder Domains geraten. Der Mandant steht im Pfad (`/api/v1/workspaces/{tenant_id}/...`), ist aber nur ein Selektor: Die aktive Mitgliedschaft des Sitzungsnutzers in genau diesem Mandanten wird bei jeder Anfrage serverseitig geprüft, sonst 404.
6. `identity.*` liegt bewusst außerhalb von RLS (Anmeldung und Mitgliedschaftsauflösung geschehen vor der Mandantenwahl). Das Schema ist nur für die Rolle `de_auth` zugänglich und wird ausschließlich über `identity/repository.py` angesprochen.

## Verworfen
* Datenbank oder Schema je Mandant: höherer Betriebsaufwand, schlechtere Auswertung über Mandanten für Administratoren, kein Sicherheitsgewinn gegenüber FORCE-RLS mit eingeschränkten Rollen.
* Reine Anwendungsfilter: ein vergessener Filter wäre ein Datenleck.

## Folgen
Jede neue Tabelle braucht `tenant_id`, die Policy und den Eintrag in den Isolationstests. `tests/integration/test_schema.py` schlägt fehl, wenn eine `app`-Tabelle ohne RLS entsteht. Abfragen müssen nicht überall zusätzlich nach `tenant_id` filtern, tun es an kritischen Stellen aber trotzdem (Tiefenverteidigung).

## Kompatibilität zu einer späteren Gesamtplattform
`identity.tenants.external_org_id` ist ein optionales, explizit gepflegtes Feld (CLI `admin set-external-org`) für eine spätere zentrale Organisations-ID. Es wird nie aus Namen oder Domains geraten. Siehe [Suite-Kompatibilität](../architecture/suite-compatibility.md).
