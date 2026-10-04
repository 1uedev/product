# ADR 0007: Keine Vektorsuche im MVP

Status: angenommen

## Entscheidung
Es gibt kein pgvector, keine Embeddings und keinen Vektorindex. Die Fundstellensuche nutzt PostgreSQL-Volltextsuche (generierte `tsvector`-Spalte mit Konfiguration `german`, `websearch_to_tsquery`, Ranking mit `ts_rank`) und `ILIKE` für Teilstrings (bei Kundennamen unterstützt von einem Trigramm-Index über `pg_trgm`, bei Fundstellen und Feedback von GIN-Indizes auf der `tsvector`-Spalte). Das Clustering im Demo-Adapter ist lexikalisch (TF-IDF), das des echten Anbieters ein Modellaufruf mit begrenztem Kontext.

## Begründung
Für wenige tausend Aussagen je Mandant reicht lexikalische Suche. Embeddings würden einen weiteren Anbieter, Kosten, Datenabfluss und eine Versionsverwaltung der Vektoren bedeuten, ohne dass die Aufgabe sie verlangt. Die Belegbindung bleibt dadurch einfach: Ein Beleg ist immer ein konkreter Chunk mit Zitat.

## Folgen
Semantisch ähnliche Aussagen ohne gemeinsame Wörter erkennt die Suche nicht. Bei deutlich größeren Datenmengen wäre ein Embedding-Index ein eigener Entwurf.
