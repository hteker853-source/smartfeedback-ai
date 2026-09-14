import asyncio

import pytest

from app import notify
from app.calle import extract_customer_text, recipient_status
from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


class _BoomCalle:
    def __init__(self, exc):
        self._exc = exc

    @property
    def enabled(self):
        return True

    def place_call(self, **kwargs):
        raise self._exc

    def fetch_call(self, calle_call_id):
        raise self._exc


def test_calle_timeout_marks_call_failed(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    order = result["order"]
    p.calle = _BoomCalle(TimeoutError("timeout"))
    call = _run(p._dispatch_call(order))
    assert call["status"] == "failed"


def test_calle_http_error_marks_call_failed(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    p.calle = _BoomCalle(RuntimeError("500 internal server error"))
    call = _run(p._dispatch_call(result["order"]))
    assert call["status"] == "failed"
    assert call["failure_code"]


def test_malformed_provider_response_empty_transcript(repo, settings):
    assert extract_customer_text({}) == ""
    assert extract_customer_text({"recipients": [{"attempts": []}]}) == ""
    assert recipient_status({}) == "failed"


def test_telegram_failure_does_not_lose_feedback(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")

    async def boom_sender(text):
        raise RuntimeError("telegram down")

    notify.set_sender(boom_sender)
    try:
        out = _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))
    finally:
        notify.set_sender(None)

    assert out is not None
    assert len(repo.list_feedbacks(p.business_id)) == 1  # feedback persisted


def test_process_crash_after_analyze_does_not_duplicate(repo, settings):
    # Simulate: DB already has feedback (e.g. process crashed right before notify),
    # then a retry must not create a second feedback.
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))
    out = _run(p.process_call_result(call["id"], "Yemek güzeldi.", simulated=True))
    assert out is None
    assert len(repo.list_feedbacks(p.business_id)) == 1
