"""Strands hooks around the deterministic policy decision point.

`run_policy_decision` (app/agents/policy_hook.py) must:
- fire real BeforeToolCallEvent/AfterToolCallEvent hooks around the call
- return EXACTLY what policy.evaluate() itself returns, for the same
  inputs — the decision logic is untouched, only the call site is wrapped
- never be reachable by an LLM (the hosting agent has no model and is
  never given a conversation turn)
"""

from app import policy
from app.agents import policy_hook
from app.schemas import Claim, ConfidenceBreakdown, Feedback, VerificationResult


def _feedback(**overrides):
    defaults = dict(sentiment="negative", satisfaction=2, priority="high")
    defaults.update(overrides)
    return Feedback(**defaults)


def _verified(claim_id, status="verified", supported=True, span_found=True):
    return VerificationResult(claim_id=claim_id, status=status, span_found=span_found, supported=supported)


def _confidence(total=0.9):
    return ConfidenceBreakdown(total=total)


def test_hooks_fire_before_and_after():
    events = []
    policy_hook._get_hook_agent()  # ensure the hook agent exists
    agent = policy_hook._hook_agent
    from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

    agent.hooks.add_callback(BeforeToolCallEvent, lambda e: events.append("before"))
    agent.hooks.add_callback(AfterToolCallEvent, lambda e: events.append("after"))

    claims = [Claim(id="c1", text="missing ayran", kind="missing_item", severity="high")]
    verifications = [_verified("c1")]
    policy_hook.run_policy_decision(
        _feedback(), claims, verifications, _confidence(), {"c1": 1},
        min_alert_confidence=0.5, recurring_threshold=3,
    )

    assert "before" in events
    assert "after" in events
    assert events.index("before") < events.index("after")


def test_decision_identical_to_calling_policy_directly():
    """Same inputs through the hook-wrapped path and the raw function must
    produce the exact same (decision, action, dissent)."""
    claims = [Claim(id="c1", text="missing ayran", kind="missing_item", severity="high")]
    verifications = [_verified("c1")]
    fb = _feedback()
    conf = _confidence()
    occurrence = {"c1": 1}

    direct = policy.evaluate(
        fb, claims, verifications, conf, occurrence,
        min_alert_confidence=0.5, recurring_threshold=3, proposed_alert=True,
    )
    via_hook = policy_hook.run_policy_decision(
        fb, claims, verifications, conf, occurrence,
        min_alert_confidence=0.5, recurring_threshold=3, proposed_alert=True,
    )

    assert via_hook[1] == direct[1]  # action
    assert via_hook[0].alert == direct[0].alert
    assert via_hook[0].insight_type == direct[0].insight_type
    assert len(via_hook[2]) == len(direct[2])  # dissent


def test_policy_tool_not_wired_into_any_llm_facing_agent():
    """policy_decision_gate must never be attached to the Feedback/
    Investigation/Trend/Supervisor agents built in strands_agents.py —
    those ARE prompted by an LLM. Only this module's own hook agent
    (never given a conversation turn) ever holds this tool, so an LLM
    can never choose to call, skip, or even see this tool."""
    import inspect

    from app.agents import strands_agents

    source = inspect.getsource(strands_agents)
    assert "policy_decision_gate" not in source
    assert "policy_hook" not in source


def test_hook_agent_keeps_no_conversation_history():
    """record_direct_tool_call=False: direct tool invocation must not
    accumulate `messages` (no model was ever called, and a long-running
    process must not grow this unboundedly across every feedback call)."""
    agent = policy_hook._get_hook_agent()
    claims = [Claim(id="c1", text="missing ayran", kind="missing_item", severity="high")]
    policy_hook.run_policy_decision(
        _feedback(), claims, [_verified("c1")], _confidence(), {"c1": 1},
        min_alert_confidence=0.5, recurring_threshold=3,
    )
    assert agent.messages == []
