import asyncio

from calle import CalleAPIError

from app.calle import CalleService, classify_outcome
from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


class _FakeCalls:
    def __init__(self):
        self.captured = {}

    def create(self, **kwargs):
        self.captured = kwargs
        return {"id": "call_fake_1"}


class _FakeClient:
    def __init__(self):
        self.calls = _FakeCalls()

    def close(self):
        pass


def test_place_call_metadata_and_idempotency(settings, monkeypatch):
    svc = CalleService(settings)
    fake = _FakeClient()
    monkeypatch.setattr(svc, "_client", lambda: fake)

    svc.place_call(
        phone="+14155550100",
        business_name="Test Biz",
        locale="en",
        order_id=42,
        correlation_id="corr-abc",
        business_id=7,
        environment="dev",
    )

    kwargs = fake.calls.captured
    assert kwargs["idempotency_key"] == "order-42"
    md = kwargs["metadata"]
    assert md["order_id"] == "42"
    assert md["correlation_id"] == "corr-abc"
    assert md["business_id"] == "7"
    assert md["environment"] == "dev"
    assert md["source"] == "smartfeedback"
    # recipient carries phone + region + locale
    recipient = kwargs["recipient"]
    assert recipient["phone"] == "+14155550100"
    assert recipient["locale"] == "en-US"
    assert recipient["region"] == "US"


class _UnsupportedRegionCalle:
    @property
    def enabled(self):
        return True

    def place_call(self, **kwargs):
        raise CalleAPIError(code="unsupported_region", message="x", status_code=422)


def test_unsupported_region_error_mapped(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905445974126", status="sent")
    p.calle = _UnsupportedRegionCalle()
    call = _run(p._dispatch_call(result["order"]))
    assert call["status"] == "failed"
    assert "unsupported_region" in call["failure_code"]
    assert "desteklenmiyor" in call["failure_code"]  # human-readable message


def test_classify_outcome_taxonomy():
    assert classify_outcome({"status": "completed"}) == "completed"
    assert classify_outcome({"status": "failed"}) == "failed"
    assert classify_outcome({"status": "canceled"}) == "canceled"
    assert classify_outcome({"status": "in_progress"}) == "in_progress"
    # no-answer / voicemail come from the recipient status, not the call status.
    assert classify_outcome(
        {"status": "completed", "recipients": [{"status": "no_answer"}]}
    ) == "no_answer"
    assert classify_outcome(
        {"status": "completed", "recipients": [{"status": "voicemail"}]}
    ) == "voicemail"
    # completed with no recipient data -> still completed (empty transcript path).
    assert classify_outcome({"status": "completed", "recipients": []}) == "completed"


def test_no_answer_not_processed_as_transcript(repo, settings, monkeypatch):
    """A call with no answer must never be processed as a completed
    conversation (no feedback, no analysis) on either attempt: the first
    no_answer schedules the single retry (DÜZELTME 2), and a second
    no_answer finalizes as terminal `no_answer` — neither ever creates a
    feedback row."""
    from app import notify
    from app.pipeline import Pipeline

    class _NoAnswerCalle:
        enabled = True

        def fetch_call(self, calle_call_id):
            return {"status": "completed", "recipients": [{"status": "no_answer"}]}

        def place_call(self, **kwargs):
            return {"id": "cal_x_retry"}

    p = Pipeline(repo, settings)
    result = p.handle_order("+905445974126", status="sent")
    order = result["order"]
    call = repo.create_call(
        order["id"], result["customer_id"], status="calling", calle_call_id="cal_x"
    )
    p.calle = _NoAnswerCalle()
    notify.set_sender(None)

    status = _run(p.poll_and_finalize(call["id"], notify_status=False))
    assert status["status"] == "retry_scheduled"
    assert repo.get_call(call["id"])["status"] == "retry_scheduled"
    assert len(repo.list_feedbacks(p.business_id)) == 0

    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    repo.update_call(call["id"], retry_at=past)
    _run(p.redial_due_calls())

    status2 = _run(p.poll_and_finalize(call["id"], notify_status=False))
    assert status2["status"] == "no_answer"
    assert repo.get_call(call["id"])["status"] == "no_answer"
    assert len(repo.list_feedbacks(p.business_id)) == 0

