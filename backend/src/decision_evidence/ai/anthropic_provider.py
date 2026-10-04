"""Anthropic adapter using the official SDK and structured outputs validated with Pydantic.

Safety properties:
* no tools, no file or network access are offered to the model; it can only return the typed JSON object
* imported text is wrapped as escaped data inside <untrusted_data> blocks; the system prompt says it is data
* every reference in the output is re-verified server-side afterwards (ai/verification.py)
* errors are mapped to stable codes; a failing real provider NEVER falls back to the demo adapter
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

import anthropic
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


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.s = settings or get_settings()
        if not self.s.anthropic_model:
            raise ProviderError("ai_not_configured", "ANTHROPIC_MODEL ist nicht gesetzt.")
        self.model = self.s.anthropic_model
        if client is None:
            if self.s.anthropic_api_key is None:
                raise ProviderError("ai_not_configured", "ANTHROPIC_API_KEY ist nicht gesetzt.")
            client = anthropic.Anthropic(api_key=self.s.anthropic_api_key.get_secret_value(),
                                         timeout=self.s.ai_timeout_seconds, max_retries=self.s.ai_max_retries)
        self.client = client

    def analyze(self, request: AnalysisRequest) -> ProviderResult:
        prompt = build_analysis_prompt(request)
        self._check_size(prompt)
        return self._call(ANALYSIS_SYSTEM, prompt, AnalysisOutput, ANALYSIS_PROMPT_VERSION)

    def draft_rationale(self, request: RationaleRequest) -> ProviderResult:
        prompt = build_rationale_prompt(request)
        self._check_size(prompt)
        return self._call(RATIONALE_SYSTEM, prompt, RationaleOutput, RATIONALE_PROMPT_VERSION)

    def _check_size(self, prompt: str) -> None:
        if len(prompt) > self.s.ai_max_input_chars:
            raise ProviderError("ai_input_too_large", "Der Kontext überschreitet das konfigurierte Größenlimit.")

    def _call(self, system: str, prompt: str, output_type: type[T], prompt_version: str) -> ProviderResult:
        started = time.perf_counter()
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=self.s.ai_max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=output_type,
            )
        except anthropic.APITimeoutError as exc:
            raise ProviderError("ai_timeout", "Der KI-Dienst hat nicht rechtzeitig geantwortet.", retryable=True) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("ai_rate_limited", "Das Anfragelimit des KI-Dienstes ist erreicht.", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("ai_unreachable", "Der KI-Dienst ist nicht erreichbar.", retryable=True) from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderError("ai_auth_failed", "Der KI-Schlüssel wurde abgelehnt.") from exc
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code >= 500 or exc.status_code == 529
            raise ProviderError("ai_request_failed" if not retryable else "ai_server_error",
                                f"Der KI-Dienst meldet einen Fehler (HTTP {exc.status_code}).", retryable=retryable) from exc
        except ValidationError as exc:
            raise ProviderError("ai_invalid_output", "Die KI-Antwort entspricht nicht dem erwarteten Schema.", retryable=True) from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderError("ai_refused", "Der KI-Dienst hat die Anfrage aus Sicherheitsgründen abgelehnt.")
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise ProviderError("ai_output_truncated", "Die KI-Antwort wurde abgeschnitten (Ausgabelimit erreicht).")
        parsed = getattr(response, "parsed_output", None)
        if parsed is None or not isinstance(parsed, output_type):
            raise ProviderError("ai_invalid_output", "Die KI-Antwort konnte nicht als strukturierte Ausgabe gelesen werden.", retryable=True)
        usage = getattr(response, "usage", None)
        return ProviderResult(
            parsed, self.name, self.model, prompt_version,
            input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
            duration_ms=int((time.perf_counter() - started) * 1000),
            meta={"request_id": getattr(response, "_request_id", None)},
        )
