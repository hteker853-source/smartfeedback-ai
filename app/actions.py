"""Deterministic downstream action executor.

Fires ONLY after human approval, to a configurable sandbox/test webhook receiver
(default OFF). The boundary is strictly enforced in code: the LLM proposes, the
policy verifies/decides, a human approves, and *this deterministic module*
performs the external side effect — never the model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def execute_downstream_action(
    url: str, timeout: float, payload: dict[str, Any], *, transport: Any = None
) -> tuple[bool, str]:
    """POST `payload` to the sandbox receiver. Returns (ok, detail).

    No retries here: callers control retry via idempotency keys. A failed or
    timed-out downstream action never breaks the call pipeline.
    """
    if not url:
        return False, "off"
    idempotency_key = payload.get("idempotency_key")
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code < 400:
            return True, f"sent:{resp.status_code}"
        return False, f"receiver_error:{resp.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.HTTPError as exc:  # noqa: BLE001
        return False, f"transport_error:{type(exc).__name__}"


def execute_approved_action(
    settings: Settings, action: dict[str, Any], case_id: int | None, *, transport: Any = None
) -> tuple[bool, str]:
    """Build a minimal, PII-free payload and fire the downstream webhook.

    The payload carries only the action identifier, type, decision and a stable
    idempotency key — never the customer phone, transcript or other PII.
    """
    url = settings.sandbox_action_webhook_url
    if not url:
        return False, "off"
    payload = {
        "action_id": action["id"],
        "action_type": action.get("action_type"),
        "decision": action.get("decision"),
        "case_id": case_id,
        "timestamp": _now(),
        "source": "smartfeedback",
        "idempotency_key": f"action:{action['id']}",
    }
    return execute_downstream_action(url, settings.sandbox_action_timeout_seconds, payload, transport=transport)
