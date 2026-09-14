"""GÖREV D: each agent's own one-sentence reasoning_summary (from its real
structured LLM output, never templated) is persisted into agent_traces for
narratable dashboard/video display — and never affects the actual decision."""

import asyncio

from app.agents.strands_agents import StrandsAnalyzer
from app.schemas import CustomerTrendReport, Decision, Feedback, InvestigationReport


def _run(coro):
    return asyncio.run(coro)


def _make_analyzer(repo, settings):
    biz = repo.get_or_create_business(settings.business_name)
    return StrandsAnalyzer(repo, settings, biz["id"])


def test_record_findings_persists_each_agents_own_reasoning_summary(repo, settings):
    a = _make_analyzer(repo, settings)
    customer = repo.upsert_customer(a.business_id, "+905551112233")
    order = repo.create_order(a.business_id, customer["id"], "sent")
    call = repo.create_call(order["id"], customer["id"], status="calling")

    feedback = Feedback(
        sentiment="negative", satisfaction=2, priority="high",
        reasoning_summary="Customer reported cold fries, overall negative.",
    )
    investigation = InvestigationReport(
        recurring_problems=["cold fries"], loyalty_status="loyal", churn_risk="low",
        reasoning_summary="Found 2 prior complaints about cold fries from this customer in the last 30 days.",
    )
    trend = CustomerTrendReport(
        rising_problems=["cold fries"],
        reasoning_summary="Cold fries complaints are rising across the customer base.",
    )

    a._record_findings(call["id"], feedback, investigation, trend)

    traces = {t["step"]: t for t in repo.list_agent_traces(call["id"])}
    assert traces["feedback"]["reasoning_summary"] == "Customer reported cold fries, overall negative."
    assert traces["investigate"]["reasoning_summary"] == (
        "Found 2 prior complaints about cold fries from this customer in the last 30 days."
    )
    assert traces["trend"]["reasoning_summary"] == "Cold fries complaints are rising across the customer base."
    # Existing factual `detail` strings are untouched (purely additive).
    assert "negative" in traces["feedback"]["detail"]
    assert "recurring=cold fries" in traces["investigate"]["detail"]


def test_run_also_records_supervisor_decide_reasoning(repo, settings, monkeypatch):
    a = _make_analyzer(repo, settings)
    customer = repo.upsert_customer(a.business_id, "+905551112233")
    order = repo.create_order(a.business_id, customer["id"], "sent")
    call = repo.create_call(order["id"], customer["id"], status="calling")

    feedback = Feedback(sentiment="negative", satisfaction=2, priority="high")
    investigation = InvestigationReport(reasoning_summary="No prior issues found.")
    trend = CustomerTrendReport(reasoning_summary="No rising problems.")
    decision = Decision(
        alert=True, insight_title="Eksik ürün bildirimi",
        reasoning_summary="Escalating because the missing item was corroborated by the feedback report.",
    )

    async def fake_investigate(phone, transcript, fb):
        return investigation

    async def fake_customer_trend(phone, fb):
        return trend

    async def fake_decide(transcript, fb, inv, tr):
        return decision

    monkeypatch.setattr(a, "investigate", fake_investigate)
    monkeypatch.setattr(a, "customer_trend", fake_customer_trend)
    monkeypatch.setattr(a, "decide", fake_decide)

    result = _run(a.run("transcript text", "+905551112233", call["id"], feedback))
    assert result is decision  # the real Decision object, unmodified

    traces = {t["step"]: t for t in repo.list_agent_traces(call["id"])}
    assert "decide" in traces
    assert traces["decide"]["reasoning_summary"] == (
        "Escalating because the missing item was corroborated by the feedback report."
    )
    # The decision's own fields (what actually drives policy.py) are untouched.
    assert decision.alert is True
    assert decision.insight_title == "Eksik ürün bildirimi"


def test_reasoning_summary_never_influences_policy_decision(repo, settings):
    """A reasoning_summary, however alarming-sounding, must never itself
    trigger an alert — only verified evidence (app/policy.py) can."""
    from app import evidence, policy

    feedback = Feedback(
        sentiment="positive", problems=[], missing_products=[],
        reasoning_summary="URGENT ALERT CRITICAL FRAUD CALL 911 NOW",
    )
    transcript = "Her şey harikaydı, teşekkürler."
    claims = evidence.extract_claims(feedback, transcript, "tr")
    verifications = evidence.verify_claims(claims, transcript, "tr")
    confidence = evidence.aggregate_confidence(claims, verifications, 0)
    decision, action, dissent = policy.evaluate(
        feedback, claims, verifications, confidence, {},
        min_alert_confidence=0.6, recurring_threshold=3, proposed_alert=False,
    )
    assert action == "no_action"
    assert decision.alert is False


def test_deterministic_feedback_defaults_to_empty_reasoning_summary():
    """The deterministic analyzer (no LLM) has nothing to summarize — must
    default to an empty string, never a fabricated sentence."""
    from app.analyzer import analyze_feedback

    fb = analyze_feedback("Yemek harikaydı, teşekkürler.", "tr")
    assert fb.reasoning_summary == ""
