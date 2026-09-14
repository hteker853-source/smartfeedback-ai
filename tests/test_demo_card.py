"""Tests for the recurring-case demo seed and the decision envelope persistence."""

import asyncio
import json

from app.pipeline import Pipeline
from app.seed import seed_recurring_case


def _run(coro):
    return asyncio.run(coro)


def test_seed_recurring_case_creates_cross_customer_case(repo, settings):
    summary = seed_recurring_case(settings, repo)
    assert summary["status"] == "seeded"
    assert summary["occurrence_count"] == 7
    assert summary["affected_customer_count"] == 6
    assert summary["impact_score"] >= 0.7  # high impact
    assert summary["trend_direction"] == "increasing"  # RISING in the dashboard label


def test_seed_recurring_case_is_idempotent(repo, settings):
    seed_recurring_case(settings, repo)
    biz = repo.get_or_create_business(settings.business_name)
    before = len(repo.list_feedbacks(biz["id"], limit=100))
    seed_recurring_case(settings, repo)  # second run must not duplicate
    after = len(repo.list_feedbacks(biz["id"], limit=100))
    assert before == after == 7


def test_decision_envelope_persisted_on_call(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    out = _run(
        p.process_call_result(call["id"], "Ayran sipariş etmiştik ama gelmedi.", simulated=True)
    )
    assert out is not None
    call_row = repo.get_call(call["id"])
    env = json.loads(call_row["decision_json"])
    assert env["action"] == "alert"
    assert "verification" in env
    assert "confidence" in env
    assert "impact" in env
    assert "claims" in env
    assert env["case_id"] is not None
