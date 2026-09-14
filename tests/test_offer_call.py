"""GÖREV 3: approved compensation-offer -> real customer call flow.

Mocks both the LLM (so tests are offline/deterministic) and the CALL-E
client (so no real call is ever placed), mirroring the patterns already used
in tests/test_calle.py and tests/test_reengagement.py."""

import asyncio

import app.calle as calle_mod
from app.db import connect as _connect
from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


class _FakeCalls:
    def __init__(self):
        self.captured = {}

    def create(self, **kwargs):
        self.captured = kwargs
        return {"id": "call_fake_offer_1"}


class _FakeClient:
    def __init__(self):
        self.calls = _FakeCalls()

    def close(self):
        pass


def _make_approved_action(repo, pipeline, phone="+905551112233", pct=40):
    customer = repo.upsert_customer(pipeline.business_id, phone)
    action = repo.create_action(
        pipeline.business_id, "compensation_offer", proposed_by="agent",
        customer_id=customer["id"],
    )
    repo.resolve_action(action["id"], f"approved:{pct}%")
    return repo.get_action(action["id"]), customer


def test_build_offer_call_task_uses_llm_when_available(settings, monkeypatch):
    """Regression test for the format-string / literal-brace bug: the system
    prompt contains a literal {"task": ...} example, which must not collide
    with the {percentage} format placeholder."""
    settings.deepseek_api_key = "fake-key-for-test"
    monkeypatch.setattr(
        calle_mod, "complete_json",
        lambda llm, system, user: {"task": "You have 48 hours to claim your 40% discount."},
    )
    from app.calle import CalleService

    svc = CalleService(settings)
    task = svc.build_offer_call_task("Acme", "en", 40)
    assert task == "You have 48 hours to claim your 40% discount."


def test_build_offer_call_task_falls_back_without_llm(settings):
    from app.calle import CalleService

    svc = CalleService(settings)  # no LLM keys in the settings fixture
    task = svc.build_offer_call_task("Acme", "en", 25)
    assert "Acme" in task
    assert "25" in task


def test_dispatch_offer_call_places_call_with_correct_idempotency_key(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    action, customer = _make_approved_action(repo, p, pct=40)

    fake = _FakeClient()
    monkeypatch.setattr(p.calle, "_client", lambda: fake)
    monkeypatch.setattr(p.settings, "calle_api_key", "fake-calle-key")

    result = _run(p._dispatch_offer_call(action))
    assert result["calle_call_id"] == "call_fake_offer_1"
    assert fake.calls.captured["idempotency_key"] == f"action-{action['id']}"
    assert fake.calls.captured["metadata"]["action_id"] == str(action["id"])
    assert "40" in fake.calls.captured["task"]
    # No PII beyond the phone number required to place the call itself.
    assert "transcript" not in fake.calls.captured


def test_dispatch_offer_call_is_idempotent(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    action, customer = _make_approved_action(repo, p, pct=40)

    fake = _FakeClient()
    monkeypatch.setattr(p.calle, "_client", lambda: fake)
    monkeypatch.setattr(p.settings, "calle_api_key", "fake-calle-key")

    result1 = _run(p._dispatch_offer_call(action))
    fake.calls.captured = {}
    result2 = _run(p._dispatch_offer_call(action))

    assert result1["id"] == result2["id"]
    assert fake.calls.captured == {}  # second dispatch never called CALL-E again

    with _connect(repo.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) c FROM outreach_calls WHERE action_id = ?", (action["id"],)
        ).fetchone()["c"]
    assert count == 1


def test_dispatch_offer_call_never_fires_for_unapproved_action(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    action = repo.create_action(
        p.business_id, "compensation_offer", proposed_by="agent", customer_id=customer["id"],
    )  # still "proposed", never resolved
    result = _run(p._dispatch_offer_call(action))
    assert result is None


def test_dispatch_offer_call_never_fires_for_rejected_action(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    action = repo.create_action(
        p.business_id, "compensation_offer", proposed_by="agent", customer_id=customer["id"],
    )
    repo.resolve_action(action["id"], "declined")
    action = repo.get_action(action["id"])
    result = _run(p._dispatch_offer_call(action))
    assert result is None


def test_dispatch_offer_call_respects_do_not_call(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    action, customer = _make_approved_action(repo, p, pct=40)
    repo.set_do_not_call(customer["id"])

    fake = _FakeClient()
    monkeypatch.setattr(p.calle, "_client", lambda: fake)
    monkeypatch.setattr(p.settings, "calle_api_key", "fake-calle-key")

    result = _run(p._dispatch_offer_call(action))
    assert result is None
    assert fake.calls.captured == {}


def test_dispatch_offer_call_marks_failed_when_calle_not_configured(repo, settings):
    p = Pipeline(repo, settings)  # settings fixture -> calle_api_key = ""
    action, customer = _make_approved_action(repo, p, pct=40)
    result = _run(p._dispatch_offer_call(action))
    assert result["status"] == "failed"
    assert result["failure_code"] == "calle_not_configured"


def test_actions_execute_approved_action_unchanged(repo, settings):
    """GÖREV 3 explicitly requires actions.py::execute_approved_action to stay
    untouched — this only re-confirms its existing sandbox-only contract."""
    from app.actions import execute_approved_action

    ok, detail = execute_approved_action(settings, {"id": 1, "action_type": "compensation_offer", "decision": "approved:40%"}, None)
    assert ok is False
    assert detail == "off"  # sandbox_action_webhook_url is empty by default
