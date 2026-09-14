"""GÖREV E: verify/add a phone-keyed, full verified-complaint-history query."""

from app.canonical import CanonicalMatcher
from app.pipeline import Pipeline
from app.schemas import Feedback, Problem


def _link_verified_problem(repo, pipeline, customer_id, name, confidence=0.8, severity="high"):
    biz = pipeline.business_id
    matcher = CanonicalMatcher(repo, pipeline.settings)
    now = "2026-09-01T00:00:00+00:00"
    canon, _ = matcher.resolve(biz, name, now)
    order = repo.create_order(biz, customer_id, "completed")
    call = repo.create_call(order["id"], customer_id, status="completed")
    fb = Feedback(problems=[Problem(text=name, severity=severity)], sentiment="negative")
    saved = repo.save_feedback(
        call_id=call["id"], order_id=order["id"], customer_id=customer_id,
        business_id=biz, feedback=fb, transcript=f"geçmişte {name} sorunu yaşandı",
    )
    repo.link_feedback_problem(saved["id"], canon["id"], name, severity)
    repo.upsert_case(
        biz, canon["id"], name, severity=severity, confidence=confidence,
        evidence_json=None, decision="alert", dissent_json=None,
        last_decision_action="alert",
    )
    return canon


def test_returns_empty_for_customer_with_no_history(repo, settings):
    p = Pipeline(repo, settings)
    repo.upsert_customer(p.business_id, "+905551112233")
    history = repo.customer_complaint_history_by_phone(p.business_id, "+905551112233")
    assert history == []


def test_returns_all_verified_complaints_by_phone(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    _link_verified_problem(repo, p, customer["id"], "cold fries", confidence=0.85)
    _link_verified_problem(repo, p, customer["id"], "missing drink", confidence=0.7)

    history = repo.customer_complaint_history_by_phone(p.business_id, "+905551112233")
    names = {h["name"] for h in history}
    assert names == {"cold fries", "missing drink"}
    # Never limited to 2/top-N (unlike customer_verified_problems) — both present.
    assert len(history) == 2


def test_does_not_cross_customers(repo, settings):
    p = Pipeline(repo, settings)
    customer_a = repo.upsert_customer(p.business_id, "+905551112233")
    customer_b = repo.upsert_customer(p.business_id, "+905559998877")
    _link_verified_problem(repo, p, customer_a["id"], "cold fries", confidence=0.85)

    assert repo.customer_complaint_history_by_phone(p.business_id, "+905559998877") == []
    history_a = repo.customer_complaint_history_by_phone(p.business_id, "+905551112233")
    assert len(history_a) == 1


def test_unverified_problem_without_case_is_excluded(repo, settings):
    """A canonical problem linked to a feedback but with NO case row (never
    verified) must not show up as a "verified complaint"."""
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    matcher = CanonicalMatcher(repo, p.settings)
    canon, _ = matcher.resolve(p.business_id, "vague complaint", "2026-09-01T00:00:00+00:00")
    order = repo.create_order(p.business_id, customer["id"], "completed")
    call = repo.create_call(order["id"], customer["id"], status="completed")
    fb = Feedback(problems=[Problem(text="vague complaint", severity="low")], sentiment="negative")
    saved = repo.save_feedback(
        call_id=call["id"], order_id=order["id"], customer_id=customer["id"],
        business_id=p.business_id, feedback=fb, transcript="belirsiz şikayet",
    )
    repo.link_feedback_problem(saved["id"], canon["id"], "vague complaint", "low")
    # No repo.upsert_case call -> never verified into a case.

    history = repo.customer_complaint_history_by_phone(p.business_id, "+905551112233")
    assert history == []
