"""Tests for adaptive pre-call contextualization and the replayable case trace."""

import asyncio

from app.calle import CalleService
from app.canonical import CanonicalMatcher
from app.pipeline import Pipeline
from app.schemas import Feedback, Problem


def _run(coro):
    return asyncio.run(coro)


def _link_verified_problem(repo, pipeline, customer_id, name, confidence=0.8):
    """Create a canonical problem + feedback link + verified case for a customer."""
    biz = pipeline.business_id
    matcher = CanonicalMatcher(repo, pipeline.settings)
    now = "2026-09-01T00:00:00+00:00"
    canon, _ = matcher.resolve(biz, name, now)
    order = repo.create_order(biz, customer_id, "completed")
    call = repo.create_call(order["id"], customer_id, status="completed")
    fb = Feedback(problems=[Problem(text=name, severity="high")], sentiment="negative")
    saved = repo.save_feedback(
        call_id=call["id"], order_id=order["id"], customer_id=customer_id,
        business_id=biz, feedback=fb, transcript=f"geçmişte {name} sorunu yaşandı",
    )
    repo.link_feedback_problem(saved["id"], canon["id"], name, "high")
    repo.upsert_case(
        biz, canon["id"], name, severity="high", confidence=confidence,
        evidence_json=None, decision="alert", dissent_json=None,
        last_decision_action="alert",
    )
    return canon


def test_build_task_appends_context(settings):
    # `settings` has no LLM keys configured, so build_task deterministically
    # exercises the no-LLM fallback path (no network call, no LLM needed).
    calle = CalleService(settings)
    base = calle.build_task("Biz", "tr")
    ctx = "cold food"
    out = calle.build_task("Biz", "tr", context=ctx)
    assert base in out
    assert ctx in out
    assert calle.build_task("Biz", "tr") == base


def test_build_call_context_empty_when_no_history(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    assert p._build_call_context(customer) is None


def test_build_call_context_uses_verified_case_only(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    _link_verified_problem(repo, p, customer["id"], "cold food", confidence=0.85)

    ctx = p._build_call_context(customer)
    assert ctx is not None
    assert "cold food" in ctx
    # Safety: the context must never leak raw transcript text.
    assert "geçmişte" not in ctx


def test_low_confidence_case_does_not_steer_context(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    _link_verified_problem(repo, p, customer["id"], "cold food", confidence=0.3)
    assert p._build_call_context(customer) is None


def test_context_does_not_cross_customers(repo, settings):
    p = Pipeline(repo, settings)
    customer_a = repo.upsert_customer(p.business_id, "+905551112233")
    customer_b = repo.upsert_customer(p.business_id, "+905559998877")
    _link_verified_problem(repo, p, customer_a["id"], "cold food", confidence=0.85)
    # customer B has no history -> no context (no cross-customer leakage)
    assert p._build_call_context(customer_b) is None


def test_case_trace_replayable(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    _link_verified_problem(repo, p, customer["id"], "cold food", confidence=0.85)
    cases = repo.list_cases(p.business_id)
    assert cases
    trace = repo.get_case_trace(cases[0]["id"])
    assert trace is not None
    assert trace["case"]["id"] == cases[0]["id"]
    assert trace["canonical_problem"]["name"] == "cold food"
    assert len(trace["feedbacks"]) >= 1
    assert trace["feedbacks"][0]["transcript"] == "geçmişte cold food sorunu yaşandı"
