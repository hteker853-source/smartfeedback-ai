import asyncio

from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def test_call_lifecycle_state_machine(repo, settings):
    p = Pipeline(repo, settings)
    # created
    result = p.handle_order("+905551112233", status="created")
    order = result["order"]
    assert order["status"] == "created"

    # created -> sent (+ scheduled_at)
    sent = repo.mark_order_sent(order["id"], 30)
    assert sent["status"] == "sent"
    assert sent["feedback_scheduled_at"] is not None

    # due -> calling (calle disabled => failed in this fixture)
    due = repo.list_orders_due_for_feedback()
    # mark scheduled_at in the past to be due
    with _open(repo.db_path) as conn:
        conn.execute(
            "UPDATE orders SET feedback_scheduled_at = datetime('now','-1 hour') WHERE id=?",
            (order["id"],),
        )
    due = repo.list_orders_due_for_feedback()
    assert len(due) == 1
    _run(p.run_due_calls())
    with _open(repo.db_path) as conn:
        call = dict(conn.execute(
            "SELECT * FROM calls WHERE order_id=?", (order["id"],)
        ).fetchone())
    assert call is not None
    # calle disabled => failed
    assert call["status"] == "failed"

    # A late transcript can still be processed once (call -> completed), but a
    # second delivery must be skipped (idempotent, no duplicate feedback).
    out = _run(p.process_call_result(call["id"], "geç gelen transkript", simulated=True))
    assert out is not None
    assert repo.get_call(call["id"])["status"] == "completed"
    out2 = _run(p.process_call_result(call["id"], "geç gelen transkript", simulated=True))
    assert out2 is None
    assert len(repo.list_feedbacks(p.business_id)) == 1


def test_completed_call_is_terminal(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    out = _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))
    assert out is not None
    c = repo.get_call(call["id"])
    assert c["status"] == "completed"
    assert c["outcome"] == "transcribed"
    # order completed too
    o = repo.get_order(result["order"]["id"])
    assert o["status"] == "completed"


def _open(db_path):
    from app.db import connect

    return connect(db_path)
