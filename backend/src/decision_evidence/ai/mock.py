"""Deterministic demo adapter. It is explicitly NOT a language model.

It clusters the imported feedback lexically (TF-IDF cosine similarity over German/English text) and builds
descriptions only from the actual data. No invented customer statements, no canned reports: different imports give
different results, identical imports give identical results. The UI labels every output of this adapter as demo.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from uuid import UUID

from decision_evidence.ai.schemas import (
    ANALYSIS_PROMPT_VERSION,
    RATIONALE_PROMPT_VERSION,
    AnalysisOutput,
    AnalysisRequest,
    ChunkInput,
    EvidenceRefOut,
    ProposedProblem,
    ProviderResult,
    RationaleClaim,
    RationaleOutput,
    RationaleRequest,
)

MOCK_MODEL = "demo-deterministic-v1"

STOPWORDS = set(["aber", "alle", "allem", "allen", "aller", "alles", "also", "auch", "auf", "aus", "bei", "bin", "bis", "bitte", "dann", "das", "dass", "dem", "den", "der", "des", "die", "dies", "diese", "diesem", "diesen", "dieser", "dieses", "doch", "dort", "durch", "ein", "eine", "einem", "einen", "einer", "eines", "einfach", "es", "etwa", "euch", "für", "gegen", "gibt", "habe", "haben", "hat", "hatte", "hier", "hin", "ich", "ihr", "ihre", "ihrem", "ihren", "ihrer", "im", "in", "ist", "ja", "jede", "jedem", "jeden", "jeder", "jetzt", "kann", "kein", "keine", "keinen", "können", "könnte", "machen", "man", "mehr", "mit", "muss", "müssen", "nach", "nicht", "noch", "nun", "nur", "oder", "ohne", "sehr", "sein", "seine", "sich", "sie", "sind", "so", "soll", "sollte", "sondern", "über", "um", "und", "uns", "unser", "unsere", "unserem", "unseren", "unserer", "von", "vor", "war", "waren", "was", "weil", "wenn", "wie", "wir", "wird", "wieder", "wollen", "wollten", "würde", "zu", "zum", "zur", "zwar", "the", "and", "for", "are", "but", "not", "you", "your", "with", "that", "this", "have", "has", "had", "was", "were", "will", "can", "could", "would", "should", "from", "they", "them", "their", "there", "what", "when", "which", "who", "how", "all", "any", "our", "out", "about", "into", "than", "then", "too", "very", "just", "also", "been", "being", "does", "did"])
POSITIVE_PHRASES = (
    "funktioniert gut", "funktioniert einwandfrei", "läuft stabil", "läuft einwandfrei", "zufrieden", "kein problem",
    "keine probleme", "brauchen wir nicht", "nicht benötigt", "kein bedarf", "keine schwierigkeiten", "works well",
    "works fine", "no issue", "not needed", "no problem", "happy with", "läuft gut", "kein thema",
)
_TOKEN = re.compile(r"[a-zäöüß][a-zäöüß0-9]+", re.IGNORECASE)   # hyphenated compounds ("CSV-Export") are split into their parts
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _stem(token: str) -> str:
    for suffix in ("ungen", "ung", "en", "er", "es", "e", "n", "s"):
        if len(token) > len(suffix) + 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in (m.group(0).lower() for m in _TOKEN.finditer(text)) if len(t) >= 3 and t not in STOPWORDS]


class _Vec:
    __slots__ = ("norm", "weights")

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights
        self.norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0

    def cosine(self, other: _Vec) -> float:
        small, large = (self.weights, other.weights) if len(self.weights) <= len(other.weights) else (other.weights, self.weights)
        return sum(w * large.get(t, 0.0) for t, w in small.items()) / (self.norm * other.norm)


def _centroid(vectors: list[_Vec]) -> _Vec:
    acc: dict[str, float] = defaultdict(float)
    for v in vectors:
        for t, w in v.weights.items():
            acc[t] += w / v.norm
    return _Vec(dict(acc))


class MockProvider:
    name = "mock"
    model = MOCK_MODEL

    def __init__(self, threshold: float = 0.15, min_cluster: int = 2) -> None:
        self.threshold, self.min_cluster = threshold, min_cluster

    # ------------------------------------------------------------------ analysis
    def analyze(self, request: AnalysisRequest) -> ProviderResult:
        started = time.perf_counter()
        chunks = sorted(request.chunks, key=lambda c: str(c.id))
        docs = [tokens(c.text) for c in chunks]
        n = len(chunks)
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        keep = {t for t, f in df.items() if f >= 2 and (n < 6 or f <= 0.6 * n)}
        idf = {t: math.log((1 + n) / (1 + df[t])) + 1.0 for t in keep}
        vecs = [_Vec({t: (1 + math.log(c)) * idf[t] for t, c in Counter(t for t in d if t in keep).items()}) for d in docs]

        merged = self._cluster(vecs)
        merged = [c for c in merged if len(c) >= self.min_cluster]
        merged.sort(key=lambda c: (-len(c), min(str(chunks[i].id) for i in c)))

        surface: dict[str, Counter[str]] = defaultdict(Counter)
        for chunk in chunks:
            for m in _TOKEN.finditer(chunk.text):
                w = m.group(0).lower()
                if w not in STOPWORDS and len(w) >= 3:
                    surface[_stem(w)][w] += 1

        problems: list[ProposedProblem] = []
        for cluster in merged[: request.max_problems]:
            centroid = _centroid([vecs[i] for i in cluster])
            top_terms = [t for t, _ in sorted(centroid.weights.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
            words = [surface[t].most_common(1)[0][0] for t in top_terms[:3]]
            evidence: list[EvidenceRefOut] = []
            for i in sorted(cluster, key=lambda i: str(chunks[i].id)):
                text = chunks[i].text
                lowered = text.lower()
                relation = "contradicts" if any(p in lowered for p in POSITIVE_PHRASES) else "supports"
                evidence.append(EvidenceRefOut(source_chunk_id=chunks[i].id, quote=self._best_sentence(text, set(top_terms)),
                                               relation=relation))
            supports = sum(1 for e in evidence if e.relation == "supports")
            problems.append(ProposedProblem(
                title=f"Thema: {', '.join(words)}",
                description=(f"Demo-Vorschlag (lexikalisches Clustering, kein Sprachmodell): {len(evidence)} Aussagen, "
                             f"davon {supports} unterstützend und {len(evidence) - supports} widersprechend. "
                             f"Häufigste Begriffe: {', '.join(surface[t].most_common(1)[0][0] for t in top_terms)}."),
                evidence=evidence))
        out = AnalysisOutput(problems=problems)
        return ProviderResult(out, self.name, self.model, ANALYSIS_PROMPT_VERSION, input_tokens=sum(len(c.text) for c in chunks) // 4,
                              output_tokens=sum(len(p.description) for p in problems) // 4,
                              duration_ms=int((time.perf_counter() - started) * 1000),
                              meta={"threshold": self.threshold, "clusters": len(merged)})

    def _cluster(self, vecs: list[_Vec]) -> list[list[int]]:
        """Agglomerative clustering with centroid linkage (deterministic, order independent).

        Repeatedly merges the two most similar clusters while their centroid cosine similarity is >= threshold.
        A lazy priority queue keeps this O(n^2 log n) for the few hundred chunks a single analysis may contain.
        """
        import heapq

        active: dict[int, tuple[list[int], _Vec]] = {i: ([i], v) for i, v in enumerate(vecs) if v.weights}
        version = dict.fromkeys(active, 0)
        heap: list[tuple[float, int, int, int, int]] = []
        ids = sorted(active)
        for ai, a in enumerate(ids):
            for b in ids[ai + 1:]:
                sim = active[a][1].cosine(active[b][1])
                if sim >= self.threshold:
                    heap.append((-sim, a, b, 0, 0))
        heapq.heapify(heap)
        next_id = len(vecs)
        while heap:
            neg, a, b, va, vb = heapq.heappop(heap)
            if a not in active or b not in active or version[a] != va or version[b] != vb:
                continue
            members = sorted(active[a][0] + active[b][0])
            del active[a], active[b]
            centroid = _centroid([vecs[i] for i in members])
            for other in sorted(active):
                sim = centroid.cosine(active[other][1])
                if sim >= self.threshold:
                    lo, hi = (other, next_id) if other < next_id else (next_id, other)
                    heapq.heappush(heap, (-sim, lo, hi, version.get(lo, 0), version.get(hi, 0)))
            active[next_id] = (members, centroid)
            version[next_id] = 0
            next_id += 1
        return [members for members, _ in active.values()]

    @staticmethod
    def _best_sentence(text: str, terms: set[str]) -> str:
        sentences = [s.strip() for s in _SENTENCE.split(text) if s.strip()]
        if not sentences:
            return text.strip()[:300]
        scored = sorted(sentences, key=lambda s: (-sum(1 for t in tokens(s) if t in terms), sentences.index(s)))
        return scored[0][:300]

    # ------------------------------------------------------------------ rationale
    def draft_rationale(self, request: RationaleRequest) -> ProviderResult:
        started = time.perf_counter()
        res = request.results
        by_name = {v["name"]: (k, v) for k, v in res.items()}
        claims: list[RationaleClaim] = []

        def result_claim(criterion: str, name: str, text: str) -> None:
            if name in by_name:
                claims.append(RationaleClaim(criterion=criterion, statement=text, result_ids=[by_name[name][0]]))

        uc = by_name.get("unique_customers", (None, {"value": 0}))[1]["value"]
        sc = by_name.get("statement_count", (None, {"value": 0}))[1]["value"]
        result_claim("customer_reach", "unique_customers",
                     f"Das Problem wird von {uc} eindeutigen Kunden genannt ({sc} Aussagen insgesamt; mehrere Aussagen eines Kunden zählen nur einmal als Kunde).")
        contra = by_name.get("contradicting_statements", (None, {"value": 0}))[1]["value"]
        if contra:
            result_claim("consistency", "contradicting_statements", f"Dem stehen {contra} widersprechende Aussagen gegenüber, die in der Entscheidung berücksichtigt werden müssen.")
        for name, label in (("arr_by_currency", "ARR der betroffenen Kunden"), ("open_pipeline_attributed", "ausdrücklich zugeordnete offene Pipeline"),
                            ("lost_volume_attributed", "ausdrücklich zugeordnetes verlorenes Volumen")):
            if name in by_name:
                v = by_name[name][1]["value"]
                parts = ", ".join(f"{amt} {cur}" for cur, amt in v["by_currency"].items()) or "keine bekannten Beträge"
                unknown = f"; bei {v['unknown_count']} Posten unbekannt" if v["unknown_count"] else ""
                result_claim("value", name, f"{label} (je Währung getrennt, nicht addiert): {parts}{unknown}.")
        supports = [e for e in request.evidence if e.relation == "supports"][:3]
        for e in supports:
            claims.append(RationaleClaim(criterion="evidence_quality", statement=f"Originalbeleg: „{e.text[:200]}“", source_chunk_ids=[e.id]))
        if request.options:
            names = " und ".join(f"„{o.get('name', '?')}“" for o in request.options[:2])
            summary_tail = f" Verglichen werden die Optionen {names}."
        else:
            summary_tail = ""
        out = RationaleOutput(
            summary=(f"Demo-Entwurf (regelbasiert, kein Sprachmodell) zu „{request.problem_title}“: {uc} Kunden, {sc} Aussagen."
                     f"{summary_tail} Die Entscheidung trifft das Team, dieser Text fasst nur berechnete Kennzahlen und Originalbelege zusammen."),
            claims=claims,
            open_questions=["Welche Annahmen der Initiative sind noch nicht validiert?"],
            caveats=["Kennzahlen sind Beobachtungen aus importierten Daten, keine Prognose von Mehrumsatz."],
        )
        return ProviderResult(out, self.name, self.model, RATIONALE_PROMPT_VERSION, input_tokens=len(request.problem_title) // 4,
                              output_tokens=len(out.summary) // 4, duration_ms=int((time.perf_counter() - started) * 1000))


def chunk_ids(chunks: list[ChunkInput]) -> set[UUID]:
    return {c.id for c in chunks}
