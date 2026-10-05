"""Adapter for a local model served by Ollama (https://ollama.com), using its HTTP API and JSON-schema constrained output.

Safety properties are the same as for the hosted adapter:
* no tools, no file or network access are offered to the model; it can only return the typed JSON object
* imported text is wrapped as escaped data inside <untrusted_data> blocks (ai/prompts.py)
* every reference in the output is re-verified server-side afterwards (ai/verification.py), which matters even more
  for small local models: invented chunk ids and non-verbatim quotes are dropped there, whatever the model returns
* errors are mapped to stable codes; a failing Ollama server NEVER falls back to the demo adapter

Ollama specifics handled here:
* the context window (num_ctx) is small by default and Ollama truncates an oversized prompt silently, which would cut
  the instructions. The adapter therefore refuses prompts that do not fit and also fails when the server reports a
  prompt that filled the whole window.
* the first request after a model was unloaded can take long (loading into memory): the timeout is separate and generous.
* nothing leaves the operator's network, so no API key and no cost are recorded.
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from decision_evidence.ai.prompts import (
    ANALYSIS_SYSTEM,
    RATIONALE_SYSTEM,
    build_analysis_prompt,
    build_rationale_prompt,
)
from decision_evidence.ai.schemas import (
    ANALYSIS_PROMPT_VERSION,
    RATIONALE_PROMPT_VERSION,
    AnalysisOutput,
    AnalysisRequest,
    ProviderError,
    ProviderResult,
    RationaleOutput,
    RationaleRequest,
)
from decision_evidence.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)

# German text needs roughly 3 to 4 characters per token; 2.5 is a deliberately pessimistic bound for the size check
CHARS_PER_TOKEN = 2.5


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self.s = settings or get_settings()
        if not self.s.ollama_model:
            raise ProviderError("ai_not_configured", "OLLAMA_MODEL ist nicht gesetzt.")
        self.model = self.s.ollama_model
        self.num_ctx = self.s.ollama_num_ctx
        # leave at least half of the window to the answer, never ask for more than the operator allows
        self.num_predict = max(256, min(self.s.ai_max_output_tokens, self.num_ctx // 2))
        self.client = client or httpx.Client(
            base_url=self.s.ollama_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.s.ollama_timeout_seconds, connect=10.0),
            trust_env=False,  # a local server is addressed directly, never through a proxy from the environment
        )

    def analyze(self, request: AnalysisRequest) -> ProviderResult:
        prompt = build_analysis_prompt(request)
        self._check_size(ANALYSIS_SYSTEM, prompt)
        return self._call(ANALYSIS_SYSTEM, prompt, AnalysisOutput, ANALYSIS_PROMPT_VERSION)

    def draft_rationale(self, request: RationaleRequest) -> ProviderResult:
        prompt = build_rationale_prompt(request)
        self._check_size(RATIONALE_SYSTEM, prompt)
        return self._call(RATIONALE_SYSTEM, prompt, RationaleOutput, RATIONALE_PROMPT_VERSION)

    def _check_size(self, system: str, prompt: str) -> None:
        if len(prompt) > self.s.ai_max_input_chars:
            raise ProviderError("ai_input_too_large", "Der Kontext überschreitet das konfigurierte Größenlimit.")
        estimated_tokens = int((len(system) + len(prompt)) / CHARS_PER_TOKEN)
        if estimated_tokens > self.num_ctx - self.num_predict:
            raise ProviderError(
                "ai_input_too_large",
                "Der Kontext passt nicht in das Kontextfenster des lokalen Modells. "
                "Weniger Quellen analysieren oder OLLAMA_NUM_CTX erhöhen (benötigt mehr Arbeitsspeicher).",
            )

    def _call(self, system: str, prompt: str, output_type: type[T], prompt_version: str) -> ProviderResult:
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "format": output_type.model_json_schema(),
            "keep_alive": self.s.ollama_keep_alive,
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict},
        }
        started = time.perf_counter()
        try:
            response = self.client.post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError("ai_timeout", "Das lokale Modell hat nicht rechtzeitig geantwortet.", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError("ai_unreachable", "Der lokale Ollama-Server ist nicht erreichbar.", retryable=True) from exc
        if response.status_code != 200:
            raise self._status_error(response)
        try:
            data = response.json()
            content = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("ai_invalid_output", "Die Antwort des Ollama-Servers hat ein unerwartetes Format.", retryable=True) from exc
        if data.get("done_reason") == "length":
            raise ProviderError("ai_output_truncated", "Die Antwort des lokalen Modells wurde abgeschnitten (Ausgabelimit erreicht).")
        prompt_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")
        if isinstance(prompt_tokens, int) and prompt_tokens >= self.num_ctx - 8:
            raise ProviderError(
                "ai_input_too_large",
                "Der Kontext hat das Kontextfenster des lokalen Modells ausgefüllt und wurde womöglich gekürzt. "
                "Weniger Quellen analysieren oder OLLAMA_NUM_CTX erhöhen.",
            )
        try:
            parsed = output_type.model_validate_json(content)
        except ValidationError as exc:
            # a model that does not follow the schema is worth one more try
            raise ProviderError("ai_invalid_output", "Die Antwort des lokalen Modells entspricht nicht dem erwarteten Schema.", retryable=True) from exc
        return ProviderResult(
            parsed, self.name, self.model, prompt_version,
            input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            meta={"done_reason": data.get("done_reason"), "num_ctx": self.num_ctx},
        )

    def _status_error(self, response: httpx.Response) -> ProviderError:
        status = response.status_code
        detail = ""
        try:
            detail = str(response.json().get("error", ""))
        except (ValueError, AttributeError):
            pass
        if status == 404 and "not found" in detail.lower():
            return ProviderError(
                "ai_model_missing",
                f"Das Modell „{self.model}“ ist auf dem Ollama-Server nicht vorhanden. Auf dem Server ausführen: ollama pull {self.model}",
            )
        if status in {401, 403}:
            return ProviderError("ai_auth_failed", "Der Ollama-Server (oder ein vorgeschalteter Proxy) hat die Anfrage abgelehnt.")
        if status >= 500 or status == 429:
            return ProviderError("ai_server_error", f"Der Ollama-Server meldet einen Fehler (HTTP {status}).", retryable=True)
        return ProviderError("ai_request_failed", f"Der Ollama-Server hat die Anfrage abgelehnt (HTTP {status}).")
