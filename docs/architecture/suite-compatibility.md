# Kompatibilität zu einer späteren Produktsuite

Das Werkzeug ist eigenständig lauffähig und enthält **keine** Anbindung an andere Produkte. Folgende Stellen sind so gelegt, dass eine spätere Integration ohne Umbau des Kerns möglich ist. Nichts davon ist gegen eine echte Plattform getestet.

| Thema | Vorbereitung im MVP | Nicht umgesetzt |
|---|---|---|
| Organisationszuordnung | `identity.tenants.external_org_id` (eindeutig, optional), gepflegt über `python -m decision_evidence.tools.admin set-external-org` | automatische Synchronisation oder Ableitung aus Namen und Domains |
| Identität | Standard-OIDC, Nutzer über `(issuer, subject)` eindeutig; Rollen in der eigenen Datenbank | zentrale Rollenverwaltung, SCIM, Gruppenmapping |
| Schnittstelle | versionierte REST-API unter `/api/v1`, OpenAPI in `docs/openapi.json`, einheitliches Fehlerformat, Kennungen sind UUIDs | Webhooks, Service-Tokens für Maschinenzugriff |
| Ereignisse | versionierter Ereignisumschlag in der Outbox (`schema_version`), siehe ADR 0004 | Veröffentlichung nach außen |
| Daten | stabile Exporte (Markdown, Belege-CSV, Bewertungs-CSV) einer Revision, Importformate für Kunden, Chancen und Feedback | Konnektoren zu CRM- oder Ticketsystemen |
| Konfiguration | alle Adressen und Geheimnisse aus Umgebungsvariablen, ein Browser-Ursprung (`PUBLIC_ORIGIN`) hinter dem Gateway | Einbettung in ein fremdes Gateway (Pfadpräfix ist im Gateway fest `/`) |
