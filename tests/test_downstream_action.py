"""Tests for the deterministic downstream action executor (Phase 3)."""

import json

import httpx

from app.actions import execute_approved_action, execute_downstream_action
from app.config import Settings


def _settings(**kw):
    return Settings(
        database_path="/tmp/x.db", aws_access_key_id="", aws_secret_access_key="",
        deepseek_api_key="", calle_api_key="", telegram_bot_token="",
        telegram_admin_chat_id="", **kw,
    )


def _body(request):
    return json.loads(request.content)


def _ok_handler(request):
    assert request.headers.get("Idempotency-Key") == "action:7"
    assert _body(request)["action_type"] == "compensation_offer"
    return httpx.Response(200, json={"received": True})


def test_execute_off_when_no_url():
    s = _settings(sandbox_action_webhook_url="")
    ok, detail = execute_approved_action(s, {"id": 7, "action_type": "compensation_offer", "decision": "approved:50%"}, None)
    assert ok is False
    assert detail == "off"


def test_execute_success():
    ok, detail = execute_downstream_action(
        "https://sandbox.example.com/hook", 5.0,
        {"action_id": 7, "action_type": "compensation_offer", "idempotency_key": "action:7"},
        transport=httpx.MockTransport(_ok_handler),
    )
    assert ok is True
    assert detail == "sent:200"


def test_execute_receiver_error():
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    ok, detail = execute_downstream_action(
        "https://sandbox.example.com/hook", 5.0,
        {"idempotency_key": "x"}, transport=httpx.MockTransport(handler),
    )
    assert ok is False
    assert detail == "receiver_error:500"


def test_execute_transport_error():
    def handler(request):
        raise httpx.ConnectError("down")

    ok, detail = execute_downstream_action(
        "https://sandbox.example.com/hook", 5.0,
        {"idempotency_key": "x"}, transport=httpx.MockTransport(handler),
    )
    assert ok is False
    assert detail.startswith("transport_error:")


def test_execute_payload_has_no_pii():
    s = _settings(sandbox_action_webhook_url="https://sandbox.example.com/hook")
    action = {"id": 9, "action_type": "compensation_offer", "decision": "approved:25%"}
    captured = {}

    def handler(request):
        captured["body"] = _body(request)
        return httpx.Response(200)

    ok, detail = execute_approved_action(s, action, None, transport=httpx.MockTransport(handler))
    assert ok is True
    body = captured["body"]
    assert body["action_id"] == 9
    assert body["decision"] == "approved:25%"
    assert body["idempotency_key"] == "action:9"
    # No PII in the downstream payload.
    assert "phone" not in body
    assert "transcript" not in body
