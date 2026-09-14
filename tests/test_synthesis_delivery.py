"""GÖREV B: nightly_synthesis (computation) is separated from
deliver_pending_synthesis (Telegram delivery). Computation timing is
unchanged; delivery is a separate, independently-schedulable step, and no
insight is lost between the two."""

import asyncio

from app import notify
from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def _seed_feedback(repo, p, transcript="Ayran gelmedi."):
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], transcript, simulated=True))


def test_computed_insight_starts_undelivered(repo, settings):
    p = Pipeline(repo, settings)
    _seed_feedback(repo, p)
    insights = _run(p.nightly_synthesis())
    assert len(insights) == 1
    assert insights[0]["notified"] == 0  # pending delivery, not yet sent

    pending = repo.list_undelivered_insights(p.business_id, "daily_summary")
    assert len(pending) == 1
    assert pending[0]["id"] == insights[0]["id"]


def test_deliver_pending_synthesis_sends_and_marks_delivered(repo, settings):
    p = Pipeline(repo, settings)
    _seed_feedback(repo, p)
    _run(p.nightly_synthesis())

    sent_messages = []

    async def fake_sender(text):
        sent_messages.append(text)

    notify.set_sender(fake_sender)
    try:
        delivered = _run(p.deliver_pending_synthesis())
    finally:
        notify.set_sender(None)

    assert delivered == 1
    assert len(sent_messages) == 1
    assert "Günlük özet" in sent_messages[0]

    # No longer pending after delivery.
    assert repo.list_undelivered_insights(p.business_id, "daily_summary") == []


def test_deliver_pending_synthesis_is_idempotent(repo, settings):
    p = Pipeline(repo, settings)
    _seed_feedback(repo, p)
    _run(p.nightly_synthesis())

    sent_messages = []
    notify.set_sender(lambda text: sent_messages.append(text) or asyncio.sleep(0))
    try:
        first = _run(p.deliver_pending_synthesis())
        second = _run(p.deliver_pending_synthesis())
    finally:
        notify.set_sender(None)

    assert first == 1
    assert second == 0  # nothing left to deliver -> no re-send
    assert len(sent_messages) == 1


def test_deliver_pending_synthesis_does_not_lose_backlog(repo, settings):
    """If delivery is skipped for a day (e.g. Telegram was down), the next
    delivery run must catch up on ALL pending summaries, not just the latest."""
    p = Pipeline(repo, settings)

    # Two independent daily_summary insights, both still pending.
    repo.save_insight(
        p.business_id, insight_type="daily_summary", title="Day 1",
        description="day 1 desc", priority="medium", recommended_action="",
        period_start="2026-09-10", period_end="2026-09-11T00:00:00+00:00",
    )
    repo.save_insight(
        p.business_id, insight_type="daily_summary", title="Day 2",
        description="day 2 desc", priority="medium", recommended_action="",
        period_start="2026-09-11", period_end="2026-09-12T00:00:00+00:00",
    )

    sent_messages = []
    notify.set_sender(lambda text: sent_messages.append(text) or asyncio.sleep(0))
    try:
        delivered = _run(p.deliver_pending_synthesis())
    finally:
        notify.set_sender(None)

    assert delivered == 2
    assert len(sent_messages) == 2
    assert "day 1 desc" in sent_messages[0]  # oldest first, nothing dropped
    assert "day 2 desc" in sent_messages[1]


def test_other_insight_types_are_unaffected_by_delivery_queue(repo, settings):
    """Only daily_summary insights go through the notified/pending queue;
    other insight types (already delivered via their own alert path) must
    not show up here."""
    p = Pipeline(repo, settings)
    repo.save_insight(
        p.business_id, insight_type="missing_item", title="X",
        description="desc", priority="high", recommended_action="",
    )
    pending = repo.list_undelivered_insights(p.business_id, "daily_summary")
    assert pending == []
