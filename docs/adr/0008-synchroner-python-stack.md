# ADR 0008: Synchrones SQLAlchemy mit Threadpool

Status: angenommen

## Entscheidung
API und Worker nutzen synchrones SQLAlchemy 2 mit psycopg 3. FastAPI führt synchrone Endpunkte im Threadpool aus. Pro Anfrage gibt es genau eine Session; der Mandantenkontext hängt an der Transaktion, nicht an einer globalen Variable.

## Begründung
Der Mandantenkontext per `set_config(..., true)` und der `after_begin`-Hook sind in der synchronen Variante einfach und nachweisbar korrekt. Die Last (Importe, Analysen, Exporte) ist durch Hintergrundjobs und Datenbank begrenzt, nicht durch gleichzeitige offene Verbindungen. Ein asynchroner Stack hätte mehr Fehlerquellen bei Kontextweitergabe und Lease-Logik gebracht.

## Folgen
Die Zahl gleichzeitiger Anfragen ist durch Threadpool und Connection-Pool begrenzt (konfigurierbar, siehe [Kapazität und Grenzen](../operations/capacity-and-limits.md)). Lange Arbeit gehört in Jobs und nie in eine Anfrage.
