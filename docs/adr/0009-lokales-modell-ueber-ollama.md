# ADR 0009: Lokales Sprachmodell über Ollama

Status: angenommen

## Kontext
Manche Betreiber dürfen oder wollen Kundenfeedback nicht an einen gehosteten KI-Dienst senden. Der Port `AIProvider` (ADR 0006) war dafür vorbereitet.

## Entscheidung
* Dritter Adapter `ollama` neben `mock` und `anthropic`, ausgewählt über `AI_PROVIDER=ollama`.
* Zugriff über die HTTP-API von Ollama (`POST /api/chat`) mit `httpx`, ohne zusätzliche Abhängigkeit. Die Antwort wird mit dem JSON-Schema des Pydantic-Modells erzwungen (`format`), `temperature=0`, kein Streaming, keine Werkzeuge.
* Prompts und serverseitige Belegprüfung sind unverändert und mit dem Anthropic-Adapter geteilt (`ai/prompts.py`, `ai/verification.py`). Ein lokales Modell erhält damit keine weitergehenden Rechte und keine laxere Prüfung.
* Ollama kürzt zu große Prompts still und würde dabei die Anweisungen verlieren. Der Adapter prüft daher vorab die Größe gegen das Kontextfenster (pessimistische Zeichen-pro-Token-Schätzung) und bricht ab, wenn der Server ein vollständig gefülltes Fenster meldet.
* Kein Rückfall auf die Demo-KI bei Fehlern, stabile Fehlercodes wie beim Anthropic-Adapter, zusätzlich `ai_model_missing`.
* Kosten werden nicht erfasst (lokale Inferenz). Das Aufrufbudget je Workspace gilt weiter.
* Betrieb: Standardadresse `host.docker.internal` für Ollama auf dem Host, optionales Compose-Overlay `compose.ollama.yaml` mit eigenem Container (Image mit Digest, Port nicht veröffentlicht).
* Migration 0005 erweitert die Prüfbedingung von `app.ai_runs.provider` um `ollama`.

## Verworfen
* OpenAI-kompatible Schnittstelle von Ollama: weniger Kontrolle über Kontextfenster und Optionen, die für die Kürzungsproblematik nötig sind.
* Automatischer Modell-Download beim Start: braucht Internetzugang zur Laufzeit und macht den Start unvorhersehbar. Das Modell wird bewusst vom Betreiber geladen.
* Zugangsschlüssel für den Ollama-Server: nicht gefordert, Ollama gehört ins interne Netz. Als Erweiterung möglich.

## Folgen
Die Qualität hängt vom gewählten Modell ab und wurde nicht gemessen. Der Adapter ist gegen Stubs und einen echten Ollama-Server ohne Modell geprüft, nicht gegen eine echte Textgenerierung.
