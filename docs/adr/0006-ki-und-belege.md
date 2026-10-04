# ADR 0006: KI nur mit prüfbaren Belegen

Status: angenommen

## Entscheidung
* Die Anwendung kennt einen Port `AIProvider` mit zwei Adaptern: `mock` (deterministisch, ausdrücklich „Demo-KI“) und `anthropic`. Der Betriebsmodus ist Konfiguration, kein Codepfad; im Produktionsmodus startet die API und der Worker mit `mock` nicht.
* Ausgaben sind typisiert (Pydantic) und werden **serverseitig** gegen den erlaubten Kontext geprüft: Chunk-IDs müssen existieren und zum Mandanten gehören, Zitate müssen wörtlich im Chunk stehen, jede Behauptung braucht einen Beleg. Details in [KI-Datenfluss](../architecture/ai-dataflow.md).
* Zahlen im Begründungstext kommen ausschließlich aus berechneten Ergebnissen (`calc:<problem>:<kennzahl>:v1`), nicht aus dem Modell. Rohquelle und Rechenergebnis sind getrennte Belegarten.
* Die KI schlägt vor (`proposed`, `ai_draft`), Menschen bestätigen. Aufwand, Erfolgswahrscheinlichkeit und Freigabe erzeugt sie nie.
* Kein stiller Rückfall: Fehler des echten Anbieters werden sichtbar, nicht durch den Demo-Adapter ersetzt.
* Jeder Aufruf wird in `app.ai_runs` protokolliert (Anbieter, Modell, Prompt-Version, Eingabe-Hash, Tokens, Prüfergebnis). Prompt-Inhalte werden nicht im Log ausgegeben.

* Weitere Adapter folgen demselben Muster, siehe [ADR 0009](0009-lokales-modell-ueber-ollama.md) für ein lokales Modell über Ollama.

## Folgen
Ein Modell kann Belege nicht erfinden, weil unverifizierte Verweise verworfen werden; es kann aber weiterhin schlecht clustern oder gewichten. Deshalb korrigiert der Mensch Cluster und bestätigt Belege (`human_verified`). Die Qualität des Anthropic-Adapters im Echtbetrieb ist nicht gemessen (siehe `docs/VERIFICATION.md`).
