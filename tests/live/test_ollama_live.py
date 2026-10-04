"""Checks against a REAL Ollama server. Skipped unless OLLAMA_TEST_URL is set (see docs/operations/ollama.md).

* Without a model on the server only the error mapping is exercised (unreachable, model missing).
* With OLLAMA_TEST_MODEL set to a model that is installed, one real analysis runs end to end through the adapter
  and the server-side verification. The model output is not deterministic enough to assert on clusters, so the test
  asserts the contract (typed output, verified references, token counts) and not the quality.
"""

from __future__ import annotations

import os
import uuid

import pytest

from decision_evidence.ai import verification as v
from decision_evidence.ai.ollama_provider import OllamaProvider
from decision_evidence.ai.schemas import AnalysisOutput, AnalysisRequest, ChunkInput, ProviderError
from decision_evidence.config import Settings

URL = os.environ.get("OLLAMA_TEST_URL")
MODEL = os.environ.get("OLLAMA_TEST_MODEL")
pytestmark = pytest.mark.skipif(not URL, reason="OLLAMA_TEST_URL not set")

TEXTS = [
    "Der CSV-Export bricht bei großen Tabellen mit mehr als 50000 Zeilen ab.",
    "Beim Export nach CSV kommt ein Fehler, die Datei ist unvollständig.",
    "Unsere IT verlangt SAML Single Sign-on, ohne SSO dürfen wir das Tool nicht einführen.",
    "SSO per SAML fehlt, das ist für uns ein Ausschlusskriterium.",
]


def settings(model: str, **kw: object) -> Settings:
    return Settings(ai_provider="ollama", ollama_base_url=URL or "", ollama_model=model, ollama_timeout_seconds=600.0, **kw)  # type: ignore[arg-type]


def request() -> tuple[AnalysisRequest, dict[uuid.UUID, str]]:
    chunks = [ChunkInput(uuid.uuid4(), t) for t in TEXTS]
    return AnalysisRequest(chunks), {c.id: c.text for c in chunks}


def test_missing_model_is_reported_with_the_pull_command() -> None:
    with pytest.raises(ProviderError) as info:
        OllamaProvider(settings("does-not-exist:1b")).analyze(request()[0])
    assert info.value.code == "ai_model_missing" and not info.value.retryable and "ollama pull does-not-exist:1b" in info.value.message


def test_unreachable_server_is_retryable() -> None:
    p = OllamaProvider(Settings(ai_provider="ollama", ollama_base_url="http://127.0.0.1:9", ollama_model="x", ollama_timeout_seconds=5.0))
    with pytest.raises(ProviderError) as info:
        p.analyze(request()[0])
    assert info.value.code == "ai_unreachable" and info.value.retryable


@pytest.mark.skipif(not MODEL, reason="OLLAMA_TEST_MODEL not set")
def test_real_model_returns_typed_output_that_survives_verification() -> None:
    req, allowed = request()
    result = OllamaProvider(settings(MODEL or "")).analyze(req)
    assert isinstance(result.output, AnalysisOutput) and result.provider == "ollama"
    assert result.input_tokens and result.output_tokens and result.duration_ms is not None
    verified, report = v.verify_analysis(result.output, allowed)
    # whatever the model proposed, nothing unverified may come out of the verification
    for problem in verified:
        for ev in problem.evidence:
            assert ev.source_chunk_id in allowed and v.find_verbatim(allowed[ev.source_chunk_id], ev.quote) is not None
    assert report.rejected_problems >= 0
