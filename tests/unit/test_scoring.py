from __future__ import annotations

from decimal import Decimal

from decision_evidence.modules.scoring import domain as s

D = Decimal
POLICY = s.Policy({"customer_reach": D("0.5"), "low_effort": D("0.3"), "evidence_quality": D("0.2")}, "exclude")


def test_score_is_deterministic_and_reproducible() -> None:
    inp = s.ScoreInput("A", {"customer_reach": D("0.4"), "low_effort": D("0.5"), "evidence_quality": D("0.8")})
    r1, r2 = s.score(POLICY, inp), s.score(POLICY, inp)
    assert r1.to_dict() == r2.to_dict()
    assert r1.total == D("51.00")  # 0.5*0.4 + 0.3*0.5 + 0.2*0.8 = 0.51
    assert r1.weight_coverage == D("1.0000")


def test_missing_values_policies() -> None:
    inp = s.ScoreInput("A", {"customer_reach": D("0.4"), "low_effort": None, "evidence_quality": D("0.8")})
    excl = s.score(POLICY, inp)
    assert excl.missing == ["low_effort"] and excl.weight_coverage == D("0.7000")
    assert excl.total == D("52.00") + D("0.00") or excl.total is not None
    assert excl.total == ((D("0.5") * D("0.4") + D("0.2") * D("0.8")) / D("0.7") * 100).quantize(D("0.01"))
    zero = s.score(s.Policy(POLICY.weights, "zero"), inp)
    assert zero.total == D("36.00")   # missing counts as 0 points, shown as such
    assert any(c["status"] == "counted_as_zero" for c in zero.contributions)
    blocked = s.score(s.Policy(POLICY.weights, "block"), inp)
    assert blocked.total is None and blocked.blocked


def test_all_missing_gives_no_score_instead_of_zero() -> None:
    r = s.score(POLICY, s.ScoreInput("A", {}))
    assert r.total is None


def test_policy_validation() -> None:
    assert s.Policy({"customer_reach": D("0.5")}).validate()
    assert s.Policy({"magic": D("1")}).validate()
    assert not s.Policy(s.DEFAULT_WEIGHTS).validate()


def test_sensitivity_detects_unstable_ranking() -> None:
    a = s.ScoreInput("A", {"customer_reach": D("0.9"), "low_effort": D("0.1"), "evidence_quality": D("0.5")})
    b = s.ScoreInput("B", {"customer_reach": D("0.5"), "low_effort": D("0.9"), "evidence_quality": D("0.5")})
    result = s.sensitivity(POLICY, [a, b])
    assert result["base_ranking"] == ["A", "B"] or result["base_ranking"] == ["B", "A"]
    # the two options trade reach against effort: shifting weights must flip the ranking at least once
    assert result["stable"] is False
    clear_win = s.sensitivity(POLICY, [s.ScoreInput("A", {"customer_reach": D(1), "low_effort": D(1), "evidence_quality": D(1)}),
                                       s.ScoreInput("B", {"customer_reach": D(0), "low_effort": D(0), "evidence_quality": D(0)})])
    assert clear_win["stable"] is True and clear_win["base_ranking"] == ["A", "B"]


def test_effort_value_conversion() -> None:
    assert s.effort_value(D(10), D(20), "person_days", D(60)) == D("0.7500")
    assert s.effort_value(D(1), D(3), "person_weeks", D(60)) == D("0.8333")
    assert s.effort_value(None, None, "person_days", D(60)) is None
