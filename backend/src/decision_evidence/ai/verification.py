"""Server-side verification of AI output against the context that was actually allowed.

A structurally valid JSON is not proof of truth: ids must belong to the permitted context (never another tenant's
chunk), quotes must appear verbatim in the cited chunk, computed-result references must exist in the catalog.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from decision_evidence.ai.schemas import AnalysisOutput, RationaleOutput

MAX_QUOTE = 600


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed, casefolded text plus a map from normalised index to original index."""
    out: list[str] = []
    mapping: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        ch = unicodedata.normalize("NFKC", ch)
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                mapping.append(i)
            prev_space = True
            continue
        for c in ch.casefold():
            out.append(c)
            mapping.append(i)
        prev_space = False
    return "".join(out).rstrip(), mapping[: len("".join(out).rstrip())]


def find_verbatim(chunk_text: str, quote: str) -> str | None:
    """Returns the exact substring of ``chunk_text`` matching ``quote`` (ignoring case and whitespace differences)."""
    q, _ = _norm_with_map(quote.strip())
    if len(q) < 3:
        return None
    hay, mapping = _norm_with_map(chunk_text)
    pos = hay.find(q)
    if pos < 0:
        return None
    start = mapping[pos]
    end = mapping[pos + len(q) - 1] + 1
    return chunk_text[start:end][:MAX_QUOTE]


@dataclass
class VerifiedEvidence:
    chunk_id: UUID
    quote: str
    relation: str


@dataclass
class VerifiedProblem:
    title: str
    description: str
    evidence: list[VerifiedEvidence]


@dataclass
class VerificationReport:
    accepted_problems: int = 0
    rejected_problems: int = 0
    accepted_evidence: int = 0
    rejected_evidence: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"accepted_problems": self.accepted_problems, "rejected_problems": self.rejected_problems,
                "accepted_evidence": self.accepted_evidence, "rejected_evidence_count": len(self.rejected_evidence),
                "rejected_evidence": self.rejected_evidence[:50]}


def verify_analysis(output: AnalysisOutput, allowed_chunks: dict[UUID, str]) -> tuple[list[VerifiedProblem], VerificationReport]:
    report = VerificationReport()
    verified: list[VerifiedProblem] = []
    for problem in output.problems:
        seen: set[UUID] = set()
        evidence: list[VerifiedEvidence] = []
        for ev in problem.evidence:
            text = allowed_chunks.get(ev.source_chunk_id)
            if text is None:
                report.rejected_evidence.append({"chunk_id": str(ev.source_chunk_id), "reason": "chunk_not_in_allowed_context"})
                continue
            quote = find_verbatim(text, ev.quote)
            if quote is None:
                report.rejected_evidence.append({"chunk_id": str(ev.source_chunk_id), "reason": "quote_not_verbatim"})
                continue
            if ev.source_chunk_id in seen:
                continue
            seen.add(ev.source_chunk_id)
            evidence.append(VerifiedEvidence(ev.source_chunk_id, quote, ev.relation))
        if not any(e.relation == "supports" for e in evidence):
            report.rejected_problems += 1
            continue
        report.accepted_problems += 1
        report.accepted_evidence += len(evidence)
        verified.append(VerifiedProblem(problem.title.strip()[:300], problem.description.strip(), evidence))
    return verified, report


def verify_rationale(output: RationaleOutput, allowed_chunks: set[UUID], allowed_results: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keeps only claims whose every reference exists in the allowed context and which cite at least one reference."""
    kept: list[dict[str, Any]] = []
    rejected = 0
    for claim in output.claims:
        refs_ok = all(c in allowed_chunks for c in claim.source_chunk_ids) and all(r in allowed_results for r in claim.result_ids)
        if not refs_ok or not (claim.source_chunk_ids or claim.result_ids):
            rejected += 1
            continue
        kept.append({"criterion": claim.criterion, "statement": claim.statement,
                     "source_chunk_ids": [str(c) for c in claim.source_chunk_ids], "result_ids": claim.result_ids})
    draft = {"summary": output.summary, "claims": kept, "open_questions": output.open_questions, "caveats": output.caveats}
    return draft, {"accepted_claims": len(kept), "rejected_claims": rejected}
