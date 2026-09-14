# NOT: Bu dosya şu an aktif olarak kullanılmıyor, gelecek bir geliştirme için hazırlanmıştır.
"""Strands tools exposed to the supervisor agent.

Each tool wraps a real, idempotent side effect. The supervisor agent calls these
itself during its tool loop; the pipeline uses the same primitives on the
deterministic fallback path.

Tools (never auto-apply a commercial action):
- lookup_history_tool
- match_canonical_problem_tool
- save_feedback_tool
- send_telegram_alarm_tool
- update_dnc_list_tool
- record_agent_trace_tool
- propose_human_approval_tool
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import strands

from .. import notify
from ..canonical import CanonicalMatcher
from ..repository import Repository
from ..schemas import Feedback


def build_tools(
    repo: Repository,
    business_id: int,
    matcher: CanonicalMatcher,
) -> list[Any]:
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @strands.tool(
        description="Look up a customer's history and the business's recent canonical problems by phone number."
    )
    def lookup_history_tool(phone: str) -> dict[str, Any]:
        """Look up customer history and recent problems.

        Parameters:
          phone: The customer phone number (E.164).
        """
        customer = repo.get_customer(business_id, phone)
        problems = [
            {"name": p["name"], "count": p["count"], "status": p["status"]}
            for p in repo.list_canonical_problems(business_id)[:10]
        ]
        if customer is None:
            return {"found": False, "problems": problems}
        return {
            "found": True,
            "customer_id": customer["id"],
            "order_count": customer["order_count"],
            "loyalty_status": customer["loyalty_status"],
            "churn_risk": customer["churn_risk"],
            "do_not_call": bool(customer["do_not_call"]),
            "problems": problems,
        }

    @strands.tool(
        description="Find the closest canonical problem for a phrase (read-only; does not change counts)."
    )
    def match_canonical_problem_tool(text: str) -> dict[str, Any]:
        """Match a problem phrase to its canonical problem.

        Parameters:
          text: The problem phrase (e.g. \"patates soğuktu\").
        """
        return matcher.match(business_id, text)

    @strands.tool(
        description="Persist structured feedback for a call and link canonical problems. Idempotent per call id."
    )
    def save_feedback_tool(call_id: int, feedback_json: str) -> dict[str, Any]:
        """Save feedback and canonicalize its problems.

        Parameters:
          call_id: The internal call id.
          feedback_json: JSON string of the Feedback object (satisfaction, sentiment,
            positives, problems, category, priority, urgency, missing_products,
            recommended_action, language, summary).
        """
        if repo.feedback_exists(call_id):
            return {"status": "already_exists", "call_id": call_id}
        call = repo.get_call(call_id)
        if call is None:
            return {"status": "call_not_found", "call_id": call_id}
        try:
            feedback = Feedback.model_validate(json.loads(feedback_json))
        except Exception as exc:  # noqa: BLE001
            return {"status": "invalid_json", "error": str(exc)[:200]}

        saved = repo.save_feedback(
            call_id=call_id,
            order_id=call["order_id"],
            customer_id=call["customer_id"],
            business_id=business_id,
            feedback=feedback,
            transcript=call.get("transcript") or "",
        )
        now = _now()
        problem_items = [(p.text, p.severity) for p in feedback.problems]
        for m in feedback.missing_products:
            problem_items.append((f"missing {m.name}", "high"))
        canon_ids: list[int] = []
        for text, severity in problem_items:
            canon, _created = matcher.resolve(business_id, text, now)
            repo.link_feedback_problem(saved["id"], canon["id"], text, severity)
            canon_ids.append(canon["id"])
        return {"status": "saved", "feedback_id": saved["id"], "canonical_ids": canon_ids}

    @strands.tool(
        description="Send an immediate Telegram alert message to the business admin."
    )
    async def send_telegram_alarm_tool(message: str) -> str:
        """Send a Telegram alert.

        Parameters:
          message: The alert text to send.
        """
        await notify.send_admin(message)
        return "sent"

    @strands.tool(
        description="Mark a customer as do-not-call; they will never be called again."
    )
    def update_dnc_list_tool(phone: str) -> str:
        """Add a customer to the do-not-call list.

        Parameters:
          phone: The customer phone number (E.164).
        """
        customer = repo.get_customer(business_id, phone)
        if customer is not None:
            repo.set_do_not_call(customer["id"])
            return "updated"
        return "customer_not_found"

    @strands.tool(
        description="Record an agent tool-call trace step for the dashboard."
    )
    def record_agent_trace_tool(call_id: int, step: str, detail: str) -> str:
        """Record an observability trace step.

        Parameters:
          call_id: The internal call id.
          step: The step name (e.g. feedback / investigate / decide).
          detail: A short human-readable description.
        """
        repo.record_agent_trace(call_id, step, detail)
        return "recorded"

    @strands.tool(
        description="Propose a compensation offer for human approval. Does NOT apply any discount."
    )
    def propose_human_approval_tool(phone: str, reason: str) -> dict[str, Any]:
        """Propose a compensation offer (recorded, never auto-applied).

        Parameters:
          phone: The customer phone number (E.164).
          reason: Why the offer is proposed.
        """
        action = repo.create_action(
            business_id, "compensation_offer", proposed_by="agent", reason=reason
        )
        return {"action_id": action["id"], "status": "proposed"}

    return [
        lookup_history_tool,
        match_canonical_problem_tool,
        save_feedback_tool,
        send_telegram_alarm_tool,
        update_dnc_list_tool,
        record_agent_trace_tool,
        propose_human_approval_tool,
    ]
