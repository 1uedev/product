"""Transparent, deterministic scoring with sensitivity analysis. No AI involved.

score = 100 * sum(w_i * v_i) over the criteria with known values (weights renormalised when the missing-value
policy is "exclude"). All arithmetic uses Decimal; identical inputs always give identical results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

FORMULA_VERSION = "v1"
CRITERIA = ("customer_reach", "arr_exposure", "pipeline_at_stake", "evidence_quality", "consistency", "low_effort", "low_risk")
CRITERION_LABELS = {
    "customer_reach": "Kundenreichweite (Anteil betroffener Kunden)",
    "arr_exposure": "ARR-Betroffenheit (Anteil am bekannten ARR der Policy-Währung)",
    "pipeline_at_stake": "Pipeline im Spiel (Anteil an offener Pipeline der Policy-Währung)",
    "evidence_quality": "Belegqualität (verifiziert und aktuell)",
    "consistency": "Widerspruchsfreiheit (Anteil unterstützender Aussagen)",
    "low_effort": "Geringer Aufwand (relativ zur Referenz)",
    "low_risk": "Geringes Risiko (menschliche Einschätzung)",
}
MISSING_POLICIES = ("exclude", "zero", "block")
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")
RISK_VALUES = {"low": Decimal(1), "medium": Decimal("0.5"), "high": Decimal(0)}


@dataclass(frozen=True)
class Policy:
    weights: dict[str, Decimal]
    missing_value_policy: str = "exclude"
    parameters: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        problems = []
        unknown = set(self.weights) - set(CRITERIA)
        if unknown:
            problems.append(f"Unbekannte Kriterien: {', '.join(sorted(unknown))}")
        if any(w < 0 for w in self.weights.values()):
            problems.append("Gewichte dürfen nicht negativ sein.")
        total = sum(self.weights.values(), Decimal(0))
        if abs(total - 1) >= Decimal("0.0001"):
            problems.append(f"Die Gewichte müssen sich zu 1 summieren (aktuell {total}).")
        if self.missing_value_policy not in MISSING_POLICIES:
            problems.append("Ungültige Regel für fehlende Werte.")
        return problems


@dataclass(frozen=True)
class ScoreInput:
    name: str
    values: dict[str, Decimal | None]
    notes: dict[str, str] = field(default_factory=dict)


@dataclass
class ScoreResult:
    name: str
    total: Decimal | None
    contributions: list[dict[str, Any]]
    weight_coverage: Decimal
    missing: list[str]
    blocked: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "total": None if self.total is None else str(self.total),
                "contributions": self.contributions, "weight_coverage": str(self.weight_coverage),
                "missing": self.missing, "blocked": self.blocked}


def clamp01(x: Decimal) -> Decimal:
    return max(Decimal(0), min(Decimal(1), x))


def score(policy: Policy, inp: ScoreInput, weights: dict[str, Decimal] | None = None) -> ScoreResult:
    w = {c: Decimal(v) for c, v in (weights or policy.weights).items() if Decimal(v) > 0}
    known = {c for c in w if inp.values.get(c) is not None}
    missing = sorted(set(w) - known)
    total_weight = sum(w.values(), Decimal(0))
    known_weight = sum((w[c] for c in known), Decimal(0))
    coverage = (known_weight / total_weight).quantize(Q4) if total_weight else Decimal(0)
    if missing and policy.missing_value_policy == "block":
        return ScoreResult(inp.name, None, [], coverage, missing, True)
    if policy.missing_value_policy == "exclude":
        denominator = known_weight
    else:  # zero: missing counts as 0 points, weights stay as they are
        denominator = total_weight
    contributions: list[dict[str, Any]] = []
    total = Decimal(0)
    for c in CRITERIA:
        if c not in w:
            continue
        value = inp.values.get(c)
        eff = (w[c] / denominator) if denominator and (c in known or policy.missing_value_policy == "zero") else Decimal(0)
        points = (eff * clamp01(value) * 100) if value is not None else Decimal(0)
        total += points
        contributions.append({
            "criterion": c, "label": CRITERION_LABELS[c], "weight": str(w[c].quantize(Q4)),
            "effective_weight": str(eff.quantize(Q4)), "value": None if value is None else str(clamp01(value).quantize(Q4)),
            "points": str(points.quantize(Q2, ROUND_HALF_EVEN)), "note": inp.notes.get(c, ""),
            "status": "known" if value is not None else ("counted_as_zero" if policy.missing_value_policy == "zero" else "excluded"),
        })
    if not known and policy.missing_value_policy == "exclude":
        return ScoreResult(inp.name, None, contributions, coverage, missing, False)
    return ScoreResult(inp.name, total.quantize(Q2, ROUND_HALF_EVEN), contributions, coverage, missing, False)


def rank(results: list[ScoreResult]) -> list[str]:
    scored = [r for r in results if r.total is not None]
    return [r.name for r in sorted(scored, key=lambda r: (-(r.total or 0), r.name))]


def _scenario_weights(base: dict[str, Decimal], criterion: str, factor: Decimal) -> dict[str, Decimal]:
    changed = dict(base)
    changed[criterion] = base[criterion] * factor
    total = sum(changed.values(), Decimal(0))
    return {c: v / total for c, v in changed.items()}


def sensitivity(policy: Policy, inputs: list[ScoreInput], delta: Decimal = Decimal("0.25")) -> dict[str, Any]:
    """Raise/lower each weight by ``delta`` (others renormalised) and report whether the ranking changes."""
    base_results = [score(policy, i) for i in inputs]
    base_rank = rank(base_results)
    scenarios: list[dict[str, Any]] = []
    stable = True
    positive = {c: w for c, w in policy.weights.items() if w > 0}
    ranges: dict[str, list[Decimal]] = {r.name: [r.total] for r in base_results if r.total is not None}
    for criterion in CRITERIA:
        if criterion not in positive:
            continue
        for label, factor in (("up", 1 + delta), ("down", 1 - delta)):
            w = _scenario_weights(positive, criterion, factor)
            results = [score(policy, i, w) for i in inputs]
            r = rank(results)
            changed = r != base_rank
            stable = stable and not changed
            for res in results:
                if res.total is not None:
                    ranges.setdefault(res.name, []).append(res.total)
            scenarios.append({"criterion": criterion, "direction": label, "factor": str(factor), "ranking": r,
                              "ranking_changed": changed,
                              "scores": {res.name: None if res.total is None else str(res.total) for res in results}})
    return {
        "delta": str(delta), "base_ranking": base_rank, "stable": stable if base_rank else None,
        "score_range": {name: {"min": str(min(v)), "max": str(max(v))} for name, v in ranges.items()},
        "scenarios": scenarios,
        "note": "Die Gewichte wurden je Kriterium um ±25 % verändert; stabil heißt: die Rangfolge bleibt in allen Szenarien gleich.",
    }


def effort_midpoint_days(low: Decimal | None, high: Decimal | None, unit: str) -> Decimal | None:
    values = [v for v in (low, high) if v is not None]
    if not values:
        return None
    mid = sum(values, Decimal(0)) / len(values)
    return mid * (5 if unit == "person_weeks" else 1)


def effort_value(low: Decimal | None, high: Decimal | None, unit: str, reference_days: Decimal) -> Decimal | None:
    mid = effort_midpoint_days(low, high, unit)
    if mid is None or reference_days <= 0:
        return None
    return clamp01(Decimal(1) - mid / reference_days).quantize(Q4)


DEFAULT_WEIGHTS = {
    "customer_reach": Decimal("0.25"), "arr_exposure": Decimal("0.20"), "pipeline_at_stake": Decimal("0.15"),
    "evidence_quality": Decimal("0.15"), "consistency": Decimal("0.10"), "low_effort": Decimal("0.10"), "low_risk": Decimal("0.05"),
}
DEFAULT_PARAMETERS = {"currency": "EUR", "effort_reference_days": 60, "fresh_days": 180}
