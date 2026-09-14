"""Makes the deterministic policy decision point (`app/policy.py::evaluate`)
observable through genuine Strands hooks, without changing its logic.

`policy.evaluate` is plain, LLM-independent Python — it is the authoritative
decision gate this whole project is built around (see README "Evidence,
verification & deterministic governance"). This module does not touch that
function at all. It only wraps the *call site* so `BeforeToolCallEvent` /
`AfterToolCallEvent` genuinely fire around it, for observability (logging).

Two things are deliberate:

- The wrapping tool is invoked *directly* (`agent.tool.policy_decision_gate(...)`),
  never through an LLM prompt loop, and the hosting `Agent` has no model and
  is never given a system prompt or conversation turn. An LLM can never
  choose to call this tool, skip it, or see it in a tool list — it isn't
  reachable that way at all.
- The tool takes no meaningful arguments and its own (Strands) return value
  is a plain status string. Both the real input objects (`Feedback`,
  `Claim`, ...) and the real `(Decision, action, dissent)` result are
  passed through module-level side channels instead of the tool's
  argument/result path — Strands coerces tool arguments into plain dicts
  before the function body runs, which would silently strip these
  dataclass/Pydantic types `policy.evaluate` relies on.
"""

from __future__ import annotations

import logging
from typing import Any

import strands
from strands import Agent
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from .. import policy

logger = logging.getLogger(__name__)

_pending_args: dict[str, Any] | None = None
_last_result: tuple[Any, str, list[Any]] | None = None


class PolicyDecisionHooks(HookProvider):
    """Read-only observer for the deterministic decision gate. Never sets
    `cancel_tool` and never touches the tool result — it only logs."""

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _before(self, event: BeforeToolCallEvent) -> None:
        logger.info("policy_decision_gate: before deterministic decision")

    def _after(self, event: AfterToolCallEvent) -> None:
        logger.info(
            "policy_decision_gate: after deterministic decision (error=%s)",
            event.exception is not None,
        )


@strands.tool(
    description=(
        "Runs the deterministic policy decision gate. Internal only: never "
        "attached to an LLM-facing agent, only ever invoked directly by "
        "code so the decision point is observable via Strands hooks. Takes "
        "no meaningful arguments — real inputs come from a side channel "
        "(see module docstring)."
    )
)
def policy_decision_gate() -> dict[str, str]:
    global _last_result
    assert _pending_args is not None, "run_policy_decision must set _pending_args first"
    _last_result = policy.evaluate(**_pending_args)
    return {"status": "ok"}


_hook_agent: Agent | None = None


def _get_hook_agent() -> Agent:
    global _hook_agent
    if _hook_agent is None:
        # No model: this agent is never prompted, only used for its direct
        # (non-LLM) tool-call machinery so hooks fire around a plain
        # function call. record_direct_tool_call=False so repeated calls
        # (this runs on every feedback call, in a long-running process)
        # don't grow `messages` unboundedly — there is no conversation to
        # keep a transcript of here.
        _hook_agent = Agent(
            tools=[policy_decision_gate],
            hooks=[PolicyDecisionHooks()],
            callback_handler=None,
            record_direct_tool_call=False,
        )
    return _hook_agent


def run_policy_decision(
    feedback: Any,
    claims: Any,
    verifications: Any,
    confidence: Any,
    occurrence_by_claim: Any,
    *,
    min_alert_confidence: float,
    recurring_threshold: int,
    proposed_alert: bool = False,
    impact: Any = None,
) -> tuple[Any, str, list[Any]]:
    """Same signature and return value as `policy.evaluate` — calls it
    through a direct Strands tool invocation so BeforeToolCallEvent/
    AfterToolCallEvent genuinely fire around the decision point."""
    global _pending_args, _last_result
    _pending_args = dict(
        feedback=feedback,
        claims=claims,
        verifications=verifications,
        confidence=confidence,
        occurrence_by_claim=occurrence_by_claim,
        min_alert_confidence=min_alert_confidence,
        recurring_threshold=recurring_threshold,
        proposed_alert=proposed_alert,
        impact=impact,
    )
    _last_result = None
    agent = _get_hook_agent()
    agent.tool.policy_decision_gate()
    assert _last_result is not None
    result = _last_result
    _pending_args = None
    return result
