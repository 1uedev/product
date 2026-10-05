from __future__ import annotations

from decimal import Decimal

from decision_evidence.ai.anthropic_provider import AnthropicProvider
from decision_evidence.ai.mock import MockProvider
from decision_evidence.ai.ollama_provider import OllamaProvider
from decision_evidence.ai.port import AIProvider
from decision_evidence.config import Settings, get_settings


def get_provider(settings: Settings | None = None) -> AIProvider:
    s = settings or get_settings()
    if s.ai_provider == "anthropic":
        return AnthropicProvider(s)
    if s.ai_provider == "ollama":
        return OllamaProvider(s)
    return MockProvider()


def estimate_cost(settings: Settings, input_tokens: int | None, output_tokens: int | None) -> tuple[Decimal, str, str] | None:
    """Only with a configured price basis; otherwise cost stays unrecorded (NULL), never guessed."""
    if (settings.ai_provider != "anthropic" or settings.ai_price_input_per_mtok is None
            or settings.ai_price_output_per_mtok is None or input_tokens is None or output_tokens is None):
        return None
    cost = (Decimal(str(settings.ai_price_input_per_mtok)) * input_tokens + Decimal(str(settings.ai_price_output_per_mtok)) * output_tokens) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001")), settings.ai_price_currency, settings.ai_price_basis or "configured prices"
