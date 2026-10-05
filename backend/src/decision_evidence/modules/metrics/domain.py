"""Evidence metrics for problems. Pure, deterministic, no database access.

Rules enforced here (specification, "Verbindliche fachliche Regeln"):
* frequency counts unique customers and, separately, the number of statements
* ARR, annual sales, open pipeline and lost volume are reported separately, per currency, never added together
* currencies are never mixed; missing values stay unknown (counted, never treated as 0)
* a customer who appears in several problems enters a portfolio total once
* evidence age and coverage are reported; a loud minority is flagged
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

METHOD_VERSION = "v1"
CENT = Decimal("0.01")


@dataclass(frozen=True)
class CustomerRef:
    id: UUID
    name: str
    segment: str | None
    value: Decimal | None
    basis: str            # arr | annual_sales | unknown
    currency: str | None


@dataclass(frozen=True)
class OpportunityRef:
    id: UUID
    customer_id: UUID
    stage: str            # open | won | lost | no_decision
    amount: Decimal | None
    currency: str | None


@dataclass(frozen=True)
class EvidenceRef:
    id: UUID
    chunk_id: UUID
    feedback_id: UUID
    customer_id: UUID | None
    opportunity_id: UUID | None
    relation: str         # supports | contradicts | context
    occurred_at: datetime
    human_verified: bool


MoneyByCurrency = dict[str, Decimal]


def _money_add(target: MoneyByCurrency, currency: str, amount: Decimal) -> None:
    target[currency] = (target.get(currency, Decimal(0)) + amount).quantize(CENT)


@dataclass
class MoneyBlock:
    """Sums per currency plus the number of items whose amount is unknown. Never one grand total."""

    by_currency: MoneyByCurrency
    unknown_count: int
    counted: int

    def as_dict(self) -> dict[str, Any]:
        return {"by_currency": {c: str(v) for c, v in sorted(self.by_currency.items())},
                "unknown_count": self.unknown_count, "counted": self.counted}


@dataclass
class ProblemMetrics:
    supporting_statements: int
    supporting_customers: int
    unassigned_statements: int
    contradicting_statements: int
    contradicting_customers: int
    conflicted_customers: int           # customers with supporting AND contradicting statements
    context_statements: int
    top_customer_share: Decimal | None
    segments: list[dict[str, Any]]
    arr: MoneyBlock
    annual_sales: MoneyBlock
    pipeline_attributed: MoneyBlock      # open opportunities referenced by supporting evidence
    lost_attributed: MoneyBlock          # lost opportunities referenced by supporting evidence
    pipeline_customer_level: MoneyBlock  # all open opportunities of affected customers (context, not attributed)
    lost_customer_level: MoneyBlock
    customers_without_value: int
    age: dict[str, Any]
    coverage: dict[str, Any]
    verified_share: Decimal | None
    fresh_share: Decimal | None
    warnings: list[dict[str, str]]
    affected_customer_ids: frozenset[UUID]
    fresh_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "method_version": METHOD_VERSION,
            "supporting_statements": self.supporting_statements,
            "supporting_customers": self.supporting_customers,
            "unassigned_statements": self.unassigned_statements,
            "contradicting_statements": self.contradicting_statements,
            "contradicting_customers": self.contradicting_customers,
            "conflicted_customers": self.conflicted_customers,
            "context_statements": self.context_statements,
            "top_customer_share": None if self.top_customer_share is None else str(self.top_customer_share),
            "segments": self.segments,
            "arr": self.arr.as_dict(),
            "annual_sales": self.annual_sales.as_dict(),
            "pipeline_attributed": self.pipeline_attributed.as_dict(),
            "lost_attributed": self.lost_attributed.as_dict(),
            "pipeline_customer_level": self.pipeline_customer_level.as_dict(),
            "lost_customer_level": self.lost_customer_level.as_dict(),
            "customers_without_value": self.customers_without_value,
            "age": self.age,
            "coverage": self.coverage,
            "verified_share": None if self.verified_share is None else str(self.verified_share),
            "fresh_share": None if self.fresh_share is None else str(self.fresh_share),
            "warnings": self.warnings,
            "fresh_days": self.fresh_days,
        }


def _ratio(a: int | Decimal, b: int | Decimal) -> Decimal | None:
    if not b:
        return None
    return (Decimal(a) / Decimal(b)).quantize(Decimal("0.0001"))


def money_for_customers(customers: Iterable[CustomerRef]) -> tuple[MoneyBlock, MoneyBlock, int]:
    arr: MoneyByCurrency = {}
    sales: MoneyByCurrency = {}
    arr_n = sales_n = arr_unknown = sales_unknown = 0
    unknown = 0
    for c in customers:
        if c.value is None or c.currency is None or c.basis == "unknown":
            unknown += 1
            continue
        if c.basis == "arr":
            _money_add(arr, c.currency, c.value)
            arr_n += 1
        elif c.basis == "annual_sales":
            _money_add(sales, c.currency, c.value)
            sales_n += 1
    return (MoneyBlock(arr, arr_unknown, arr_n), MoneyBlock(sales, sales_unknown, sales_n), unknown)


def money_for_opportunities(opps: Iterable[OpportunityRef], stage: str) -> MoneyBlock:
    """Each opportunity counts once (callers pass a de-duplicated iterable keyed by id)."""
    totals: MoneyByCurrency = {}
    unknown = counted = 0
    seen: set[UUID] = set()
    for o in opps:
        if o.stage != stage or o.id in seen:
            continue
        seen.add(o.id)
        if o.amount is None or o.currency is None:
            unknown += 1
        else:
            _money_add(totals, o.currency, o.amount)
            counted += 1
    return MoneyBlock(totals, unknown, counted)


def compute_problem_metrics(
    evidence: list[EvidenceRef],
    customers: dict[UUID, CustomerRef],
    opportunities: dict[UUID, OpportunityRef],
    *,
    now: datetime | None = None,
    fresh_days: int = 180,
) -> ProblemMetrics:
    """``customers`` and ``opportunities`` are the complete tenant universe (needed for coverage and shares)."""
    now = now or datetime.now(UTC)
    supports = [e for e in evidence if e.relation == "supports"]
    contradicts = [e for e in evidence if e.relation == "contradicts"]
    context = [e for e in evidence if e.relation == "context"]

    support_customers = {e.customer_id for e in supports if e.customer_id}
    contra_customers = {e.customer_id for e in contradicts if e.customer_id}
    per_customer = Counter(e.customer_id for e in supports if e.customer_id)
    top_share = _ratio(max(per_customer.values()), len(supports)) if per_customer else None

    affected = [customers[c] for c in support_customers if c in customers]
    arr, sales, unknown_value = money_for_customers(affected)

    # opportunity attribution: explicit references from supporting evidence only
    attributed = {e.opportunity_id: opportunities[e.opportunity_id] for e in supports
                  if e.opportunity_id and e.opportunity_id in opportunities}
    customer_level = {o.id: o for o in opportunities.values() if o.customer_id in support_customers}

    seg_totals = Counter((c.segment or "unbekannt") for c in customers.values())
    seg_affected = Counter((c.segment or "unbekannt") for c in affected)
    segments = [
        {"segment": seg, "affected_customers": seg_affected.get(seg, 0), "total_customers": total,
         "share": str(_ratio(seg_affected.get(seg, 0), total) or Decimal(0))}
        for seg, total in sorted(seg_totals.items())
    ]

    ages = sorted((now - e.occurred_at).days for e in supports)
    fresh = [a for a in ages if a <= fresh_days]
    age = {
        "oldest_days": ages[-1] if ages else None,
        "newest_days": ages[0] if ages else None,
        "median_days": int(statistics.median(ages)) if ages else None,
        "older_than_fresh_count": len(ages) - len(fresh),
        "fresh_days": fresh_days,
    }
    total_customers = len(customers)
    customers_with_feedback_any = None  # filled by callers that know the whole feedback universe
    coverage = {
        "affected_customers": len(support_customers),
        "total_customers": total_customers,
        "affected_share": None if not total_customers else str(_ratio(len(support_customers), total_customers)),
        "value_known_customers": len(affected) - unknown_value,
        "value_known_share": None if not affected else str(_ratio(len(affected) - unknown_value, len(affected))),
        "customers_with_any_feedback": customers_with_feedback_any,
    }
    verified = _ratio(sum(1 for e in supports if e.human_verified), len(supports))
    fresh_share = _ratio(len(fresh), len(ages))

    warnings: list[dict[str, str]] = []
    if supports and top_share is not None and top_share >= Decimal("0.5") and len(supports) >= 3:
        warnings.append({"code": "single_customer_dominates",
                         "message": f"{int(top_share * 100)} % der unterstützenden Aussagen stammen von einem einzigen Kunden."})
    share = _ratio(len(support_customers), total_customers)
    if support_customers and share is not None and share < Decimal("0.25"):
        warnings.append({"code": "minority_signal",
                         "message": f"Das Problem betrifft {len(support_customers)} von {total_customers} Kunden "
                                    f"({int(share * 100)} %). Eine laute Minderheit ist kein Nachweis für den gesamten Markt."})
    if ages and fresh_share is not None and fresh_share < Decimal("0.5"):
        warnings.append({"code": "stale_evidence",
                         "message": f"Mehr als die Hälfte der Belege ist älter als {fresh_days} Tage."})
    if unknown_value:
        warnings.append({"code": "unknown_values",
                         "message": f"Bei {unknown_value} betroffenen Kunden ist der Umsatz unbekannt (nicht als 0 gerechnet)."})
    currencies = set(arr.by_currency) | set(sales.by_currency) | set(money_for_opportunities(attributed.values(), "open").by_currency)
    if len(currencies) > 1:
        warnings.append({"code": "mixed_currencies",
                         "message": f"Mehrere Währungen ({', '.join(sorted(currencies))}) werden getrennt ausgewiesen, nicht umgerechnet."})
    if contradicts:
        warnings.append({"code": "has_contradictions",
                         "message": f"{len(contradicts)} widersprechende Aussagen von {len(contra_customers)} Kunden."})
    if len(supports) > len(support_customers) and support_customers:
        warnings.append({"code": "repeated_customers",
                         "message": f"{len(supports)} Aussagen stammen von nur {len(support_customers)} Kunden (mehrere Aussagen je Kunde)."})
    if any(e.customer_id is None for e in supports):
        warnings.append({"code": "unassigned_statements",
                         "message": "Einige Aussagen sind keinem Kunden zugeordnet und zählen nicht als Kunden."})

    return ProblemMetrics(
        supporting_statements=len(supports),
        supporting_customers=len(support_customers),
        unassigned_statements=sum(1 for e in supports if e.customer_id is None),
        contradicting_statements=len(contradicts),
        contradicting_customers=len(contra_customers),
        conflicted_customers=len(support_customers & contra_customers),
        context_statements=len(context),
        top_customer_share=top_share,
        segments=segments,
        arr=arr, annual_sales=sales,
        pipeline_attributed=money_for_opportunities(attributed.values(), "open"),
        lost_attributed=money_for_opportunities(attributed.values(), "lost"),
        pipeline_customer_level=money_for_opportunities(customer_level.values(), "open"),
        lost_customer_level=money_for_opportunities(customer_level.values(), "lost"),
        customers_without_value=unknown_value,
        age=age, coverage=coverage, verified_share=verified, fresh_share=fresh_share, warnings=warnings,
        affected_customer_ids=frozenset(support_customers), fresh_days=fresh_days,
    )


def portfolio_totals(
    per_problem_customers: dict[UUID, frozenset[UUID]],
    customers: dict[UUID, CustomerRef],
    opportunities: dict[UUID, OpportunityRef],
) -> dict[str, Any]:
    """Overall view across several problems. A customer with several related problems counts once, with their
    full value exactly once; opportunities of those customers likewise."""
    union: set[UUID] = set().union(*per_problem_customers.values()) if per_problem_customers else set()
    affected = [customers[c] for c in union if c in customers]
    arr, sales, unknown = money_for_customers(affected)
    opps = {o.id: o for o in opportunities.values() if o.customer_id in union}
    naive_sum = sum(len(v) for v in per_problem_customers.values())
    return {
        "problems": len(per_problem_customers),
        "unique_customers": len(union),
        "customer_problem_pairs": naive_sum,
        "arr": arr.as_dict(),
        "annual_sales": sales.as_dict(),
        "open_pipeline_customer_level": money_for_opportunities(opps.values(), "open").as_dict(),
        "lost_customer_level": money_for_opportunities(opps.values(), "lost").as_dict(),
        "customers_without_value": unknown,
    }


def result_catalog(problem_id: UUID, m: ProblemMetrics) -> dict[str, dict[str, Any]]:
    """Computed results with stable ids and the method used. AI statements about numbers may cite these ids
    (a separate evidence kind from raw source chunks)."""
    pid = str(problem_id)

    def entry(name: str, method: str, value: Any) -> tuple[str, dict[str, Any]]:
        return f"calc:{pid}:{name}:{METHOD_VERSION}", {"name": name, "method": method, "value": value}

    items = [
        entry("unique_customers", "Anzahl eindeutiger Kunden mit mindestens einer unterstützenden Aussage", m.supporting_customers),
        entry("statement_count", "Anzahl unterstützender Aussagen (mehrere je Kunde möglich)", m.supporting_statements),
        entry("contradicting_statements", "Anzahl widersprechender Aussagen", m.contradicting_statements),
        entry("arr_by_currency", "Summe ARR der betroffenen Kunden je Währung; unbekannte Werte nicht mitgezählt", m.arr.as_dict()),
        entry("annual_sales_by_currency", "Summe Jahresumsatz der betroffenen Kunden je Währung; getrennt von ARR", m.annual_sales.as_dict()),
        entry("open_pipeline_attributed", "Offene Chancen, die unterstützende Aussagen ausdrücklich nennen, je Währung", m.pipeline_attributed.as_dict()),
        entry("lost_volume_attributed", "Verlorene Chancen, die unterstützende Aussagen ausdrücklich nennen, je Währung", m.lost_attributed.as_dict()),
        entry("affected_customer_share", "Betroffene Kunden geteilt durch alle Kunden im Workspace", m.coverage["affected_share"]),
        entry("evidence_age", "Alter der unterstützenden Belege in Tagen", m.age),
    ]
    return dict(items)
