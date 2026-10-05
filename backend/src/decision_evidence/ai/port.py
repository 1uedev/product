from __future__ import annotations

from typing import Protocol

from decision_evidence.ai.schemas import AnalysisRequest, ProviderResult, RationaleRequest


class AIProvider(Protocol):
    name: str
    model: str

    def analyze(self, request: AnalysisRequest) -> ProviderResult: ...
    def draft_rationale(self, request: RationaleRequest) -> ProviderResult: ...
