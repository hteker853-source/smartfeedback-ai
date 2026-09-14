"""GÖREV 2: dormant-customer re-engagement outreach.

Uses a mocked LLM (monkeypatching app.calle.complete_json) so the test is
fully offline and deterministic, exactly like the rest of the suite."""

import asyncio
from datetime import datetime, timedelta, timezone

import app.calle as calle_mod
from app import notify
from app.canonical import CanonicalMatcher
from app.db import connect as _connect
from app.pipeline import DORMANT_DAYS, Pipeline
from app.schemas import Feedback, Problem


def _run(coro):
    return asyncio.run(coro)


def _make_dormant_customer_with_verified_complaint(
    repo, pipeline, phone="+905551112233", days_ago=DORMANT_DAYS + 5
):
    biz = pipeline.business_id
    matcher = CanonicalMatcher(repo, pipeline.settings)
    now_iso = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(
        timespec="seconds"
    )
    customer = repo.upsert_customer(biz, phone)
    canon, _ = matcher.resolve(biz, "cold fries", now_iso)
    order = repo.create_order(biz, customer["id"], "completed")
    call = repo.create_call(order["id"], customer["id"], status="completed")
    fb = Feedback(problems=[Problem(text="cold fries", severity="high")], sentiment="negative")
    saved = repo.save_feedback(
        call_id=call["id"], order_id=order["id"], customer_id=customer["id"],
        business_id=biz, feedback=fb, transcript="patatesler soğuktu",
    )
    repo.link_feedback_problem(saved["id"], canon["id"], "cold fries", "high")
    repo.upsert_case(
        biz, canon["id"], "cold fries", severity="high", confidence=0.85,
        evidence_json=None, decision="alert", dissent_json=None,
        last_decision_action="alert",
    )
    # Backdate last_order_at directly (update_customer_stats would use "now").
    with _connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE customers SET last_order_at = ? WHERE id = ?",
            (now_iso, customer["id"]),
        )
    return repo.get_customer(biz, phone)


def _mock_llm(monkeypatch, settings, task_text="Merhaba, sizi özledik! Patatesler bu sefer nasıldı?"):
    settings.deepseek_api_key = "fake-key-for-test"
    monkeypatch.setattr(
        calle_mod, "complete_json", lambda *a, **k: {"task": task_text}
    )


def test_list_dormant_customers_respects_cutoff(repo, settings):
    p = Pipeline(repo, settings)
    dormant = _make_dormant_customer_with_verified_complaint(repo, p)
    fresh = repo.upsert_customer(p.business_id, "+905559990000")
    with _connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE customers SET last_order_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), fresh["id"]),
        )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DORMANT_DAYS)).isoformat(
        timespec="seconds"
    )
    result = repo.list_dormant_customers(p.business_id, cutoff)
    ids = {c["id"] for c in result}
    assert dormant["id"] in ids
    assert fresh["id"] not in ids


def test_propose_reengagement_outreach_creates_action_and_notifies(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    customer = _make_dormant_customer_with_verified_complaint(repo, p)
    _mock_llm(monkeypatch, settings)

    notified = {}

    async def fake_simple_approval(text, action_id):
        notified["text"] = text
        notified["action_id"] = action_id

    notify.set_simple_approval_sender(fake_simple_approval)
    try:
        n = _run(p.propose_reengagement_outreach())
    finally:
        notify.set_simple_approval_sender(None)

    assert n == 1
    assert "action_id" in notified
    assert "cold fries" in notified["text"]

    action = repo.get_action(notified["action_id"])
    assert action["action_type"] == "reengagement_outreach"
    assert action["customer_id"] == customer["id"]
    assert action["reason"]  # the LLM-generated message was persisted


def test_propose_reengagement_outreach_is_idempotent_per_episode(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    _make_dormant_customer_with_verified_complaint(repo, p)
    _mock_llm(monkeypatch, settings)
    notify.set_simple_approval_sender(lambda text, action_id: asyncio.sleep(0))
    try:
        first = _run(p.propose_reengagement_outreach())
        second = _run(p.propose_reengagement_outreach())
    finally:
        notify.set_simple_approval_sender(None)
    assert first == 1
    assert second == 0  # same dormancy episode -> not proposed again


def test_propose_reengagement_outreach_skips_without_verified_complaint(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    with _connect(repo.db_path) as conn:
        old = (datetime.now(timezone.utc) - timedelta(days=DORMANT_DAYS + 5)).isoformat(
            timespec="seconds"
        )
        conn.execute("UPDATE customers SET last_order_at = ? WHERE id = ?", (old, customer["id"]))
    _mock_llm(monkeypatch, settings)
    n = _run(p.propose_reengagement_outreach())
    assert n == 0


def test_propose_reengagement_outreach_skips_do_not_call(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    customer = _make_dormant_customer_with_verified_complaint(repo, p)
    repo.set_do_not_call(customer["id"])
    _mock_llm(monkeypatch, settings)
    n = _run(p.propose_reengagement_outreach())
    assert n == 0


def test_dispatch_reengagement_call_is_idempotent(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    customer = _make_dormant_customer_with_verified_complaint(repo, p)
    _mock_llm(monkeypatch, settings)

    action = repo.create_action(
        p.business_id, "reengagement_outreach", proposed_by="agent",
        reason="Merhaba, sizi özledik!", customer_id=customer["id"],
    )
    repo.resolve_action(action["id"], "approved")
    action = repo.get_action(action["id"])

    # CALL-E is not configured in the test settings -> dispatch must record a
    # clean failure, never raise, and never create a second outreach_calls row.
    result1 = _run(p._dispatch_reengagement_call(action))
    result2 = _run(p._dispatch_reengagement_call(action))
    assert result1["failure_code"] == "calle_not_configured"
    assert result1["id"] == result2["id"]  # same row, not duplicated

    with _connect(repo.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) c FROM outreach_calls WHERE action_id = ?", (action["id"],)
        ).fetchone()["c"]
    assert count == 1
