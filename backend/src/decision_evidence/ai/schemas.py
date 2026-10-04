"""Typed requests and structured outputs of the AI port. Providers only ever see ids and text they are given;
every reference in the output is verified server-side (see verification.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ANALYSIS_PROMPT_VERSION = "analysis-v1"
RATIONALE_PROMPT_VERSION = "rationale-v1"


# ----------------------------------------------------------------------------- outputs (validated with Pydantic)
class EvidenceRefOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_chunk_id: UUID
    quote: str = Field(min_length=1, max_length=600, description="Wörtliches Zitat aus dem Chunk")
    relation: Literal["supports", "contradicts", "context"]


class ProposedProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(max_length=1500)
    evidence: list[EvidenceRefOut] = Field(min_length=1, max_length=60)


class AnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    problems: list[ProposedProblem] = Field(max_length=25)


class RationaleClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str = Field(max_length=60)
    statement: str = Field(min_length=3, max_length=800)
    source_chunk_ids: list[UUID] = Field(default_factory=list, max_length=10)
    result_ids: list[str] = Field(default_factory=list, max_length=10)


class RationaleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=3, max_length=1500)
    claims: list[RationaleClaim] = Field(max_length=20)
    open_questions: list[str] = Field(default_factory=list, max_length=10)
    caveats: list[str] = Field(default_factory=list, max_length=10)


# ----------------------------------------------------------------------------- requests
@dataclass(frozen=True)
class ChunkInput:
    id: UUID
    text: str
    occurred_at: datetime | None = None
    segment: str | None = None
    relation: str | None = None


@dataclass(frozen=True)
class AnalysisRequest:
    chunks: list[ChunkInput]
    max_problems: int = 12
    language: str = "de"


@dataclass(frozen=True)
class RationaleRequest:
    problem_title: str
    problem_description: str
    evidence: list[ChunkInput]
    results: dict[str, dict[str, Any]]            # result_id -> {name, method, value}
    options: list[dict[str, Any]]
    scores: list[dict[str, Any]]
    language: str = "de"


@dataclass
class ProviderResult:
    output: BaseModel
    provider: str
    model: str
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Provider call failed. ``retryable`` marks transient errors (timeout, rate limit, 5xx)."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
