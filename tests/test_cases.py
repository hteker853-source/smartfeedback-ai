"""Tests for persistent case state (cross-call recurrence)."""

from app.repository import Repository


def _biz(repo):
    return repo.get_or_create_business("Test Business")


def test_upsert_case_folds_recurrence(repo):
    biz = _biz(repo)
    canon = repo.upsert_canonical_problem(
        biz["id"], "cold fries", category="quality", vector=None,
        first_seen_at="2026-01-01T00:00:00+00:00",
        last_seen_at="2026-01-01T00:00:00+00:00", count_delta=1,
    )
    c1 = repo.upsert_case(
        biz["id"], canon["id"], "cold fries", severity="medium", confidence=0.6,
        evidence_json=None, decision="record", dissent_json=None,
        last_decision_action="record",
    )
    assert c1["occurrence_count"] == 1
    assert c1["status"] == "evidence_collected"

    c2 = repo.upsert_case(
        biz["id"], canon["id"], "cold fries", severity="high", confidence=0.8,
        evidence_json=None, decision="alert", dissent_json=None,
        last_decision_action="alert",
    )
    assert c2["id"] == c1["id"]  # same case
    assert c2["occurrence_count"] == 2
    assert c2["severity"] == "high"  # MAX(medium, high)


def test_case_status_lifecycle(repo):
    biz = _biz(repo)
    canon = repo.upsert_canonical_problem(
        biz["id"], "missing item", category="product", vector=None,
        first_seen_at="2026-01-01T00:00:00+00:00",
        last_seen_at="2026-01-01T00:00:00+00:00", count_delta=1,
    )
    case = repo.upsert_case(
        biz["id"], canon["id"], "missing item", severity="high", confidence=0.9,
        evidence_json=None, decision="alert", dissent_json=None,
        last_decision_action="alert",
    )
    repo.update_case_status(case["id"], "verified")
    assert repo.get_case(case["id"])["status"] == "verified"

    repo.record_case_outcome(case["id"], "fixed")
    updated = repo.get_case(case["id"])
    assert updated["status"] == "resolved"
    assert updated["outcome"] == "fixed"
    assert updated["resolved_at"] is not None


def test_list_cases_orders_by_confidence(repo):
    biz = _biz(repo)
    p1 = repo.upsert_canonical_problem(
        biz["id"], "a", category="product", vector=None,
        first_seen_at="2026-01-01T00:00:00+00:00",
        last_seen_at="2026-01-01T00:00:00+00:00", count_delta=1,
    )
    p2 = repo.upsert_canonical_problem(
        biz["id"], "b", category="product", vector=None,
        first_seen_at="2026-01-01T00:00:00+00:00",
        last_seen_at="2026-01-01T00:00:00+00:00", count_delta=1,
    )
    repo.upsert_case(biz["id"], p1["id"], "a", severity="low", confidence=0.4,
                     evidence_json=None, decision="record", dissent_json=None, last_decision_action="record")
    repo.upsert_case(biz["id"], p2["id"], "b", severity="high", confidence=0.9,
                     evidence_json=None, decision="alert", dissent_json=None, last_decision_action="alert")
    cases = repo.list_cases(biz["id"])
    assert cases[0]["title"] == "b"
    assert cases[0]["confidence"] == 0.9
