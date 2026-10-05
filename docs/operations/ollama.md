# Lokales Sprachmodell mit Ollama

Statt der Anthropic API kann die Analyse und der Begründungsentwurf von einem Modell laufen, das auf Ihrer eigenen Hardware über [Ollama](https://ollama.com) bereitgestellt wird.
Es verlassen dann keine Feedbackdaten den eigenen Server. Entscheidung und Freigabe bleiben menschlich, und alle Belege werden wie bei jedem Anbieter serverseitig geprüft ([KI-Datenfluss](../architecture/ai-dataflow.md)).

## Auf einen Blick

| Einstellung | Bedeutung | Standard |
|---|---|---|
| `AI_PROVIDER=ollama` | schaltet den Adapter ein | `mock` |
| `OLLAMA_MODEL` | Name des Modells, wie `ollama list` es zeigt (Pflicht), zum Beispiel `qwen3:8b` | leer |
| `OLLAMA_BASE_URL` | Adresse des Servers | `http://host.docker.internal:11434` (Ollama auf dem Docker-Host) |
| `OLLAMA_NUM_CTX` | Kontextfenster in Token. Prompt und Antwort müssen hineinpassen | `8192` |
| `OLLAMA_TIMEOUT_SECONDS` | Wartezeit auf eine Antwort (das erste Laden eines Modells kann dauern) | `300` |
| `OLLAMA_KEEP_ALIVE` | wie lange das Modell nach einer Anfrage im Speicher bleibt | `5m` |
| `AI_MAX_INPUT_CHARS` | harte Obergrenze für den Prompt in Zeichen | `120000` |
| `AI_MAX_OUTPUT_TOKENS` | gewünschte Antwortlänge. Es wird höchstens die Hälfte des Kontextfensters genutzt | `8000` |

Ein Ollama-Server ab Version 0.5 wird benötigt (strukturierte Ausgabe über ein JSON-Schema). Getestet wurde der Adapter gegen Version 0.35.1 (siehe unten, was damit geprüft wurde und was nicht).

## Variante A: Ollama läuft auf dem Docker-Host

1. Ollama auf dem Host installieren und ein Modell laden: `ollama pull qwen3:8b`.
2. Ollama muss aus den Containern erreichbar sein. Standardmäßig lauscht es nur auf `127.0.0.1` des Hosts, das ist aus einem Container **nicht** erreichbar. Setzen Sie `OLLAMA_HOST=0.0.0.0:11434`
   (oder die IP der Docker-Bridge, meist `172.17.0.1`) für den Ollama-Dienst und sperren Sie den Port per Firewall nach außen. **Ollama selbst hat keine Anmeldung.** Wer den Port erreicht, kann das Modell nutzen.
3. Starten:

```sh
AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b docker compose up --build -d
```

`compose.yaml` bildet `host.docker.internal` für API und Worker auf den Host ab (`host-gateway`), das funktioniert unter Linux und Docker Desktop.

## Variante B: Ollama als zusätzlicher Container

```sh
AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b docker compose -f compose.yaml -f compose.ollama.yaml up --build -d
docker compose -f compose.yaml -f compose.ollama.yaml exec ollama ollama pull qwen3:8b     # einmalig, braucht Internetzugang
```

* Das Overlay `compose.ollama.yaml` startet den Dienst `ollama` (Image mit festem Tag und Digest, Modelle im Volume `ollama-models`) und richtet `OLLAMA_BASE_URL` von API und Worker auf `http://ollama:11434`.
  Der Port wird **nicht** veröffentlicht, nur die Anwendung erreicht den Dienst.
* Der Container läuft ohne GPU. Für eine NVIDIA GPU das NVIDIA Container Toolkit installieren und im Overlay den auskommentierten `deploy`-Block ergänzen.
* Das offizielle Ollama-Image läuft als root. Die Anforderung „eigene App-Prozesse als Nicht-Root“ gilt für die Prozesse dieser Anwendung, nicht für das Fremd-Image.
* Produktion: die Overlay-Datei nach `compose.prod.yaml` angeben und `--env-file .env.prod` beibehalten. Es sind dann keine Anthropic-Werte nötig (`AI_PROVIDER=ollama`, `OLLAMA_MODEL`).

## Umstellen und zurück

Die Einstellung gilt für den ganzen Server. Ein Wechsel des Anbieters oder Modells ändert den Schlüssel der Analyse (Anbieter und Modell gehören dazu), bereits gespeicherte Probleme bleiben unverändert.
Die Einstellungsseite des Workspace zeigt den aktiven Anbieter („Ollama (lokales Modell …)“), Entwürfe und Analyseläufe tragen Anbieter und Modell.

## Fehlerbilder

Der Adapter wechselt bei Fehlern **nie** stillschweigend auf die Demo-KI. Der Job schlägt sichtbar fehl, die Meldung steht an der Aufgabe.

| Code | Ursache | Maßnahme |
|---|---|---|
| `ai_model_missing` | Modell nicht auf dem Server (Ollama antwortet 404) | `ollama pull <Modell>` auf dem Ollama-Server. Wird nicht wiederholt |
| `ai_unreachable` | Server nicht erreichbar | läuft Ollama, stimmt `OLLAMA_BASE_URL`, Host-Bindung beachten (Variante A). Wird mit Wartezeit wiederholt |
| `ai_timeout` | keine Antwort innerhalb von `OLLAMA_TIMEOUT_SECONDS` | größeres Zeitlimit, kleineres Modell oder weniger Quellen pro Analyse. Wird wiederholt |
| `ai_input_too_large` | Prompt passt nicht in das Kontextfenster, oder der Server meldet ein vollständig gefülltes Fenster (Ollama kürzt sonst still und würde die Anweisungen abschneiden) | weniger Quellen analysieren oder `OLLAMA_NUM_CTX` erhöhen (braucht mehr Arbeitsspeicher) |
| `ai_output_truncated` | Antwort hat das Längenlimit erreicht | `AI_MAX_OUTPUT_TOKENS` und `OLLAMA_NUM_CTX` erhöhen, kleinere Analyse |
| `ai_invalid_output` | Antwort entspricht nicht dem Schema (kleine Modelle halten das Schema nicht immer ein) | wird wiederholt; hilft nicht, ein größeres Modell wählen |
| `ai_server_error` | Ollama meldet 5xx oder 429 (zum Beispiel zu wenig Speicher für das Modell) | Log von Ollama prüfen; wird wiederholt |

## Was zu beachten ist

* **Qualität:** Kleine Modelle clustern und formulieren schwächer als große gehostete Modelle. Die serverseitige Prüfung verwirft erfundene Chunk-IDs und nicht wörtliche Zitate, sie kann aber nicht verhindern, dass ein Modell Aussagen ungünstig gruppiert.
  Deshalb bestätigen und korrigieren Menschen die Vorschläge. Welches Modell für Ihre Daten taugt, muss mit Ihren Daten ausprobiert werden. Dafür gibt es hier keine Messung und keine Empfehlung.
* **Geschwindigkeit und Speicher:** Die Laufzeit hängt von Modell und Hardware ab und kann bei CPU-Betrieb Minuten betragen. Der Worker führt standardmäßig zwei Aufgaben parallel aus, ein einzelner Ollama-Server arbeitet Anfragen je nach Einstellung nacheinander ab. Reduzieren Sie bei knapper Hardware die gleichzeitig laufenden Aufgaben je Workspace in den Einstellungen.
* **Kontextfenster:** Der Standard von 8192 Token fasst nur einige Dutzend kurze Aussagen. Die Anwendung bricht ab, statt zu kürzen. Mit der Zahl der Quellen pro Analyse und `OLLAMA_NUM_CTX` skaliert der Speicherbedarf.
* **Reasoning-Modelle** (zum Beispiel mit „Denkphase“) brauchen deutlich länger. Ob ein Modell die JSON-Schema-Vorgabe zuverlässig einhält, hängt vom Modell ab.
* **Kosten und Budget:** Es werden keine Kosten erfasst. Das monatliche Aufrufbudget je Workspace gilt weiterhin und zählt auch lokale Aufrufe.
* **Keine Anmeldung am Ollama-Server:** Der Adapter sendet keinen Zugangsschlüssel. Ein Ollama hinter einem Reverse Proxy mit Bearer-Token wird nicht unterstützt. Betreiben Sie Ollama nur im internen Netz.
* **Datenfluss:** Der Prompt (Textstellen ohne Kundennamen und Beträge, siehe [KI-Datenfluss](../architecture/ai-dataflow.md)) geht vom Worker per HTTP an `OLLAMA_BASE_URL`. Läuft Ollama auf einem anderen Rechner, ist die Verbindung unverschlüsselt, sofern Sie sie nicht selbst per TLS absichern (`https://` in `OLLAMA_BASE_URL` wird unterstützt).

## Was geprüft wurde

| Prüfung | Stand |
|---|---|
| Anfrageaufbau (Schema als `format`, `stream: false`, keine Werkzeuge, Optionen, maskierte Daten), Fehlerabbildung, Größenprüfungen, Konfiguration | Unit-Tests gegen einen simulierten Server (`tests/unit/test_ollama.py`) |
| Analyse als Job mit simuliertem Modell: nur geprüfte Vorschläge werden gespeichert, Protokoll `ai_runs` mit Anbieter `ollama`, fehlendes Modell schlägt sichtbar und ohne Wiederholung fehl | Integrationstests mit PostgreSQL (`tests/integration/test_jobs.py`) |
| **Echter Ollama-Server 0.35.1** ohne Modell: Erreichbarkeit, Fehlerbild „Modell fehlt“ aus der echten Antwort, nicht erreichbarer Server | `tests/live/test_ollama_live.py` mit `OLLAMA_TEST_URL=http://127.0.0.1:11434` |
| Ganzer Weg Oberfläche, API, Outbox, Broker, Worker, **echter Ollama-Container**, sichtbarer Fehler „Modell fehlt“ in der Oberfläche | `OLLAMA_OVERLAY=1 AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b scripts/test_e2e.sh 40-ollama` |
| **Echte Textgenerierung mit einem Modell** | **nicht geprüft.** In der Entwicklungsumgebung ist der Modell-Download (registry.ollama.ai) gesperrt, es konnte kein Modell geladen werden. Weder Qualität noch Laufzeit noch die Einhaltung des Schemas durch ein reales Modell sind gemessen |

Wer ein Modell installiert hat, kann den echten Lauf selbst prüfen:

```sh
cd backend && OLLAMA_TEST_URL=http://127.0.0.1:11434 OLLAMA_TEST_MODEL=qwen3:8b uv run pytest ../tests/live -v
E2E_OLLAMA_MODEL_INSTALLED=1 OLLAMA_OVERLAY=1 AI_PROVIDER=ollama OLLAMA_MODEL=qwen3:8b scripts/test_e2e.sh 40-ollama
```
