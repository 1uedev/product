from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from decision_evidence.modules.metrics import domain as d

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def cust(name: str, seg: str | None = "smb", value: str | None = None, basis: str = "arr", cur: str | None = "EUR") -> d.CustomerRef:
    return d.CustomerRef(uuid.uuid4(), name, seg, None if value is None else Decimal(value), basis if value else "unknown", cur if value else None)


def ev(c: d.CustomerRef | None, rel: str = "supports", days: int = 10, opp: d.OpportunityRef | None = None, verified: bool = False) -> d.EvidenceRef:
    return d.EvidenceRef(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), c.id if c else None, opp.id if opp else None, rel,
                         NOW - timedelta(days=days), verified)


def universe(customers: list[d.CustomerRef]) -> dict:
    return {c.id: c for c in customers}


def test_ten_tickets_of_one_customer_are_one_customer() -> None:
    a, b = cust("A", value="1000"), cust("B", value="500")
    evidence = [ev(a) for _ in range(10)] + [ev(b)]
    m = d.compute_problem_metrics(evidence, universe([a, b]), {}, now=NOW)
    assert m.supporting_customers == 2 and m.supporting_statements == 11
    assert m.top_customer_share is not None and m.top_customer_share > Decimal("0.9")
    assert any(w["code"] == "single_customer_dominates" for w in m.warnings)
    assert any(w["code"] == "repeated_customers" for w in m.warnings)
    assert m.arr.by_currency == {"EUR": Decimal("1500.00")}   # each customer once


def test_money_kinds_and_currencies_stay_separate_and_unknown_is_not_zero() -> None:
    a = cust("A", value="1000", basis="arr")
    b = cust("B", value="2000", basis="annual_sales")
    c = cust("C", value="300", basis="arr", cur="CHF")
    u = cust("U")  # unknown value
    opp_open = d.OpportunityRef(uuid.uuid4(), a.id, "open", Decimal("5000"), "EUR")
    opp_lost = d.OpportunityRef(uuid.uuid4(), b.id, "lost", Decimal("700"), "USD")
    opp_unknown = d.OpportunityRef(uuid.uuid4(), u.id, "open", None, None)
    evidence = [ev(a, opp=opp_open), ev(b, opp=opp_lost), ev(c), ev(u, opp=opp_unknown)]
    m = d.compute_problem_metrics(evidence, universe([a, b, c, u]), {o.id: o for o in (opp_open, opp_lost, opp_unknown)}, now=NOW)
    assert m.arr.by_currency == {"EUR": Decimal("1000.00"), "CHF": Decimal("300.00")}
    assert m.annual_sales.by_currency == {"EUR": Decimal("2000.00")}
    assert m.pipeline_attributed.by_currency == {"EUR": Decimal("5000.00")} and m.pipeline_attributed.unknown_count == 1
    assert m.lost_attributed.by_currency == {"USD": Decimal("700.00")}
    assert m.customers_without_value == 1
    assert any(w["code"] == "mixed_currencies" for w in m.warnings)
    assert any(w["code"] == "unknown_values" for w in m.warnings)
    dumped = m.to_dict()["arr"]["by_currency"]
    assert set(dumped) == {"EUR", "CHF"}  # never one grand total


def test_contradictions_age_and_coverage() -> None:
    cs = [cust(f"C{i}") for i in range(10)]
    evidence = [ev(cs[0], days=400), ev(cs[1], days=500), ev(cs[2], days=20, rel="contradicts"), ev(cs[0], rel="contradicts")]
    m = d.compute_problem_metrics(evidence, universe(cs), {}, now=NOW)
    assert m.supporting_customers == 2 and m.contradicting_customers == 2 and m.conflicted_customers == 1
    assert m.age["oldest_days"] == 500 and m.age["older_than_fresh_count"] == 2
    assert m.coverage["affected_share"] == "0.2000"
    codes = {w["code"] for w in m.warnings}
    assert {"minority_signal", "stale_evidence", "has_contradictions"} <= codes


def test_portfolio_counts_customer_once_across_problems() -> None:
    a, b = cust("A", value="1000"), cust("B", value="400")
    opp = d.OpportunityRef(uuid.uuid4(), a.id, "open", Decimal("900"), "EUR")
    cmap, omap = universe([a, b]), {opp.id: opp}
    totals = d.portfolio_totals({uuid.uuid4(): frozenset({a.id, b.id}), uuid.uuid4(): frozenset({a.id}), uuid.uuid4(): frozenset({a.id})}, cmap, omap)
    assert totals["unique_customers"] == 2 and totals["customer_problem_pairs"] == 4
    assert totals["arr"]["by_currency"] == {"EUR": "1400.00"}
    assert totals["open_pipeline_customer_level"]["by_currency"] == {"EUR": "900.00"}


def test_unassigned_statements_do_not_count_as_customers() -> None:
    a = cust("A")
    m = d.compute_problem_metrics([ev(None), ev(None), ev(a)], universe([a]), {}, now=NOW)
    assert m.supporting_customers == 1 and m.supporting_statements == 3 and m.unassigned_statements == 2


def test_result_catalog_has_stable_ids() -> None:
    a = cust("A")
    pid = uuid.uuid4()
    cat = d.result_catalog(pid, d.compute_problem_metrics([ev(a)], universe([a]), {}, now=NOW))
    assert f"calc:{pid}:unique_customers:v1" in cat and cat[f"calc:{pid}:unique_customers:v1"]["value"] == 1
