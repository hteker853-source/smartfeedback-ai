"""GÖREV A: one-time "delay the feedback call" option after an order is
marked sent. Default (no button pressed) behavior must be unaffected."""

from datetime import datetime, timedelta, timezone

from app.pipeline import Pipeline


def _make_sent_order(repo, pipeline, phone="+905551112233", delay_minutes=30):
    customer = repo.upsert_customer(pipeline.business_id, phone)
    order = repo.create_order(pipeline.business_id, customer["id"], "created")
    sent = repo.mark_order_sent(order["id"], delay_minutes)
    return sent


def test_default_behavior_unchanged_without_delay(repo, settings):
    p = Pipeline(repo, settings)
    before = datetime.now(timezone.utc)
    order = _make_sent_order(repo, p, delay_minutes=30)
    scheduled = datetime.fromisoformat(order["feedback_scheduled_at"])
    # Untouched: still ~30 minutes out, delay_used stays 0.
    assert order["delay_used"] == 0
    delta = (scheduled - before).total_seconds()
    assert 29 * 60 <= delta <= 31 * 60


def test_delay_pushes_scheduled_time_forward(repo, settings):
    p = Pipeline(repo, settings)
    order = _make_sent_order(repo, p, delay_minutes=30)
    original = datetime.fromisoformat(order["feedback_scheduled_at"])

    updated = repo.delay_feedback_call(order["id"], 120)
    assert updated is not None
    new_time = datetime.fromisoformat(updated["feedback_scheduled_at"])
    assert (new_time - original) == timedelta(minutes=120)
    assert updated["delay_used"] == 1


def test_delay_can_only_be_used_once(repo, settings):
    p = Pipeline(repo, settings)
    order = _make_sent_order(repo, p, delay_minutes=30)

    first = repo.delay_feedback_call(order["id"], 30)
    assert first is not None

    second = repo.delay_feedback_call(order["id"], 1440)
    assert second is None  # already used -> rejected, no further change

    final = repo.get_order(order["id"])
    assert final["feedback_scheduled_at"] == first["feedback_scheduled_at"]


def test_delay_on_unknown_order_returns_none(repo, settings):
    p = Pipeline(repo, settings)
    assert repo.delay_feedback_call(999999, 30) is None


def test_delay_on_order_without_schedule_returns_none(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    order = repo.create_order(p.business_id, customer["id"], "created")
    # Never marked sent -> feedback_scheduled_at is NULL.
    assert repo.delay_feedback_call(order["id"], 30) is None
