import asyncio
import sqlite3

import pytest

from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def _make_call(repo, p, phone="+905551112233", transcript="Harika."):
    result = p.handle_order(phone, status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    return call, result


def test_duplicate_process_call_result_is_idempotent(repo, settings):
    p = Pipeline(repo, settings)
    call, _ = _make_call(repo, p)
    out1 = _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))
    out2 = _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))
    assert out1 is not None
    assert out2 is None  # second processing skipped
    assert len(repo.list_feedbacks(p.business_id)) == 1


def test_feedback_unique_constraint(repo, settings):
    p = Pipeline(repo, settings)
    call, result = _make_call(repo, p)
    _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))
    # Direct duplicate insert must fail at the DB level.
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_feedback(
            call_id=call["id"], order_id=call["order_id"],
            customer_id=call["customer_id"], business_id=p.business_id,
            feedback=_dummy_feedback(), transcript="x",
        )


def test_duplicate_webhook_event_dedup(repo, settings):
    assert repo.record_webhook_event("evt-1", "call-x") is True
    assert repo.record_webhook_event("evt-1", "call-x") is False  # duplicate


def test_one_call_per_order_enforced_via_dispatch(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    order = result["order"]
    c1 = _run(p._dispatch_call(order))
    c2 = _run(p._dispatch_call(order))  # second dispatch must not duplicate
    assert c1 is not None
    assert c2 is None
    # exactly one call row for the order
    with _open(repo.db_path) as conn:
        n = conn.execute("SELECT COUNT(*) c FROM calls WHERE order_id=?", (order["id"],)).fetchone()["c"]
    assert n == 1


def _dummy_feedback():
    from app.schemas import Feedback

    return Feedback(satisfaction=5, sentiment="positive")


def _open(db_path):
    from app.db import connect

    return connect(db_path)
