"""Mock end-to-end test runner for the GÖREV 3 compensation-offer call flow.

Simulates an ALREADY-APPROVED `compensation_offer` action and runs
`Pipeline._dispatch_offer_call` against a FAKE CALL-E client — no real call is
ever placed. Exercises the LLM-generated call script (in the customer's
`preferred_language`), the action-keyed idempotency guard (`outreach_calls`),
and the "no PII beyond what's needed" boundary, without touching the real
CALL-E API or the core feedback-call pipeline (`calls`/`_dispatch_call`).

Usage:
    python -m app.mock_offer_call --phone 05445974126 --percentage 30
    python -m app.mock_offer_call --phone 05445974126 --percentage 50 --language en
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from .config import get_settings
from .db import init_db
from .pipeline import Pipeline
from .repository import Repository


class _FakeCalls:
    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.captured = kwargs
        return {"id": "call_fake_offer_1"}


class _FakeClient:
    """Stands in for the real CALL-E client so NO network call is ever made."""

    def __init__(self) -> None:
        self.calls = _FakeCalls()

    def close(self) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mock the GÖREV 3 compensation-offer call flow (no real call is placed)"
    )
    parser.add_argument("--phone", default="+905445974126")
    parser.add_argument("--percentage", type=int, default=30)
    parser.add_argument(
        "--language", default=None,
        help="override customers.preferred_language for this test run",
    )
    args = parser.parse_args()

    settings = get_settings()
    init_db(settings.database_file)
    repo = Repository(settings.database_file)
    pipeline = Pipeline(repo, settings)

    customer = repo.upsert_customer(pipeline.business_id, args.phone)
    if args.language:
        repo.set_preferred_language(customer["id"], args.language)
        customer = repo.get_customer(pipeline.business_id, args.phone)

    action = repo.create_action(
        pipeline.business_id, "compensation_offer", proposed_by="agent",
        reason="mock_offer_call test", customer_id=customer["id"],
    )
    repo.resolve_action(action["id"], f"approved:{args.percentage}%")
    action = repo.get_action(action["id"])

    # Replace the real CALL-E client with a fake one — same technique the
    # test suite uses (tests/test_calle.py) — so NO real call is placed.
    fake = _FakeClient()
    pipeline.calle._client = lambda: fake  # type: ignore[method-assign]

    print("=== GÖREV 3 MOCK TEST: compensation-offer call flow (no real call) ===")
    print(f"Phone:               {args.phone}")
    print(
        "Preferred language:  "
        f"{customer.get('preferred_language') or '(none set — falls back to business default locale)'}"
    )
    print(f"Approved percentage: {args.percentage}%")
    print(f"Action id:           {action['id']} (decision={action['decision']!r})")

    result1 = asyncio.run(pipeline._dispatch_offer_call(action))
    print("\n--- outreach_calls row after first dispatch ---")
    print(result1)

    print("\n--- Captured CALL-E request (this is what WOULD have been sent) ---")
    for key, value in fake.calls.captured.items():
        print(f"{key}: {value}")

    # Idempotency check: dispatching again for the SAME action must reuse the
    # same outreach_calls row and must NOT attempt a second CALL-E call.
    fake.calls.captured = {}
    result2 = asyncio.run(pipeline._dispatch_offer_call(action))
    print("\n--- Second dispatch (idempotency check) ---")
    print(f"Same outreach_calls row (no duplicate)?  {result1['id'] == result2['id']}")
    print(f"Second CALL-E call attempted (should be False)?  {bool(fake.calls.captured)}")

    print("\n=== DONE — no real call was placed at any point. ===")


if __name__ == "__main__":
    main()
