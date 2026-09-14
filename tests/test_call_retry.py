"""Single 5-minute no_answer/voicemail retry (DÜZELTME 2).

`poll_and_finalize` must not finalize a no_answer/voicemail call on the
first attempt: it schedules exactly one retry (`retry_count` 0 -> 1,
`retry_at` set) instead. `Pipeline.redial_due_calls` then re-places the
CALL-E call once the retry window has passed, reusing the SAME `calls`
row. A second no_answer/voicemail (retry_count already 1) finalizes the
call as terminal, same as the pre-existing no-retry behavior.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app import notify
from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


class FakeCalle:
    """Swaps in for `pipeline.calle`; queues one fetch_call result per call
    and a separate place_call result for the redial attempt."""

    def __init__(self, fetch_result):
        self.enabled = True
        self._fetch_result = fetch_result
        self.place_call_count = 0

    def fetch_call(self, calle_call_id):
        return self._fetch_result

    def place_call(self, **kwargs):
        self.place_call_count += 1
        return {"id": f"redial-{self.place_call_count}"}


def _make_call(repo, business_id, phone="+15550100900"):
    customer = repo.upsert_customer(business_id, phone)
    order = repo.create_order(
        business_id, customer["id"], status="created", order_type="service", locale="tr"
    )
    call = repo.create_call(order["id"], customer["id"], status="calling")
    repo.update_call(call["id"], calle_call_id=f"fake-{call['id']}", notify_completion=1)
    return repo.get_call(call["id"])


def test_first_no_answer_schedules_retry_not_terminal(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    call = _make_call(repo, p.business_id)
    p.calle = FakeCalle({"status": "in_progress", "recipients": [{"status": "no_answer"}]})

    captured = []
    monkeypatch.setattr(notify, "_sender", lambda text: captured.append(text) or asyncio.sleep(0))

    result = _run(p.poll_and_finalize(call["id"]))
    assert result["status"] == "retry_scheduled"

    updated = repo.get_call(call["id"])
    assert updated["status"] == "retry_scheduled"
    assert updated["retry_count"] == 1
    assert updated["retry_at"] is not None
    assert updated["completed_at"] is None  # NOT terminal yet
    assert any("tekrar denenecek" in t for t in captured)


def test_retry_then_success_completes_normally(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    call = _make_call(repo, p.business_id)
    p.calle = FakeCalle({"status": "in_progress", "recipients": [{"status": "no_answer"}]})
    monkeypatch.setattr(notify, "_sender", lambda text: asyncio.sleep(0))

    _run(p.poll_and_finalize(call["id"]))
    scheduled = repo.get_call(call["id"])
    assert scheduled["status"] == "retry_scheduled"

    # Make the retry due (backdate retry_at) and redial.
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    repo.update_call(call["id"], retry_at=past)
    n = _run(p.redial_due_calls())
    assert n == 1

    redialed = repo.get_call(call["id"])
    assert redialed["status"] == "calling"
    assert redialed["calle_call_id"] == "redial-1"
    assert redialed["retry_at"] is None
    assert p.calle.place_call_count == 1

    # Second attempt answers this time.
    p.calle._fetch_result = {
        "status": "completed",
        "recipients": [{"status": "completed", "attempts": [{"transcript_turns": [
            {"speaker": "user", "text": "her sey harikaydi"}
        ]}]}],
    }
    result2 = _run(p.poll_and_finalize(call["id"]))
    assert result2["status"] == "completed"
    final = repo.get_call(call["id"])
    assert final["status"] == "completed"


def test_second_no_answer_becomes_terminal_no_more_retries(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    call = _make_call(repo, p.business_id)
    p.calle = FakeCalle({"status": "in_progress", "recipients": [{"status": "no_answer"}]})

    captured = []
    monkeypatch.setattr(notify, "_sender", lambda text: captured.append(text) or asyncio.sleep(0))

    _run(p.poll_and_finalize(call["id"]))  # 1st no_answer -> retry_scheduled

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    repo.update_call(call["id"], retry_at=past)
    _run(p.redial_due_calls())  # redial places attempt #2

    captured.clear()
    result = _run(p.poll_and_finalize(call["id"]))  # 2nd attempt also no_answer
    assert result["status"] == "no_answer"

    final = repo.get_call(call["id"])
    assert final["status"] == "no_answer"
    assert final["completed_at"] is not None  # NOW terminal
    assert final["retry_count"] == 1  # never incremented past 1 -> exactly one retry
    assert any("cevapsız kaldı" in t and "tekrar" not in t for t in captured)


def test_redial_skips_do_not_call_customer(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    call = _make_call(repo, p.business_id, phone="+15550100901")
    repo.set_do_not_call(call["customer_id"])
    p.calle = FakeCalle({"status": "in_progress", "recipients": [{"status": "no_answer"}]})

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    repo.update_call(
        call["id"], status="retry_scheduled", retry_count=1, retry_at=past,
        outcome="no_answer",
    )
    n = _run(p.redial_due_calls())
    assert n == 0
    final = repo.get_call(call["id"])
    assert final["status"] == "failed"
    assert final["failure_code"] == "retry_skipped"
    assert p.calle.place_call_count == 0
