"""Tests for the deterministic decision gate."""

from app import policy
from app.schemas import (
    Claim,
    ConfidenceBreakdown,
    DissentEntry,
    Feedback,
    ImpactScore,
    MissingProduct,
    VerificationResult,
)


def _conf(total=0.9):
    return ConfidenceBreakdown(total=total)


def _verified(claim_id, status="verified", supported=True, span_found=True):
    return VerificationResult(claim_id=claim_id, status=status, span_found=span_found, supported=supported)


def test_verified_missing_item_alerts():
    fb = Feedback(missing_products=[MissingProduct(name="ayran")])
    claims = [Claim(id="c1", text="missing ayran", kind="missing_item", severity="high")]
    verifications = [_verified("c1")]
    decision, action, dissent = policy.evaluate(
        fb, claims, verifications, _conf(), {"c1": 1},
        min_alert_confidence=0.6, recurring_threshold=3,
    )
    assert action == "alert"
    assert decision.alert is True
    assert "ayran" in decision.alert_message.lower()


def test_unverified_missing_item_downgrades_to_record():
    fb = Feedback(missing_products=[MissingProduct(name="ayran")])
    claims = [Claim(id="c1", text="missing ayran", kind="missing_item")]
    verifications = [_verified("c1", status="unverifiable", span_found=False, supported=False)]
    decision, action, dissent = policy.evaluate(
        fb, claims, verifications, _conf(total=0.1), {"c1": 1},
        min_alert_confidence=0.6, recurring_threshold=3,
        proposed_alert=True,
    )
    assert action == "record"
    assert decision.alert is False
    assert any(d.source_agent == "verification" for d in dissent)


def test_proposed_alert_with_failed_claims_records_policy_dissent():
    fb = Feedback(missing_products=[MissingProduct(name="ayran")])
    claims = [Claim(id="c1", text="missing ayran", kind="missing_item")]
    verifications = [_verified("c1", status="unsupported", span_found=True, supported=False)]
    decision, action, dissent = policy.evaluate(
        fb, claims, verifications, _conf(total=0.2), {"c1": 1},
        min_alert_confidence=0.6, recurring_threshold=3,
        proposed_alert=True,
    )
    assert decision.alert is False
    assert any(d.source_agent == "policy" for d in dissent)


def test_recurring_problem_records_insight_no_alert():
    fb = Feedback(problems=[])
    claims = [Claim(id="c1", text="cold fries", kind="problem")]
    verifications = [_verified("c1")]
    decision, action, dissent = policy.evaluate(
        fb, claims, verifications, _conf(), {"c1": 5},
        min_alert_confidence=0.6, recurring_threshold=3,
    )
    assert action == "record"
    assert decision.alert is False
    assert decision.insight_type == "recurring_problem"


def test_no_claims_yields_no_action():
    fb = Feedback(sentiment="positive")
    decision, action, dissent = policy.evaluate(
        fb, [], [], _conf(total=0.0), {},
        min_alert_confidence=0.6, recurring_threshold=3,
    )
    assert action == "no_action"
    assert decision.alert is False


def test_multiple_verified_missing_escalates_priority():
    fb = Feedback(missing_products=[MissingProduct(name="ayran"), MissingProduct(name="kola")])
    claims = [
        Claim(id="c1", text="missing ayran", kind="missing_item"),
        Claim(id="c2", text="missing kola", kind="missing_item"),
    ]
    verifications = [_verified("c1"), _verified("c2")]
    decision, action, _ = policy.evaluate(
        fb, claims, verifications, _conf(), {"c1": 1, "c2": 1},
        min_alert_confidence=0.6, recurring_threshold=3,
    )
    assert action == "alert"
    assert decision.alert_priority == "critical"


def test_risk_adaptive_alert_on_recurring_high_severity_high_impact():
    fb = Feedback(problems=[])
    claims = [Claim(id="c1", text="late delivery", kind="problem", severity="high")]
    verifications = [_verified("c1")]
    impact = ImpactScore(score=0.8, label="high")
    decision, action, _ = policy.evaluate(
        fb, claims, verifications, _conf(total=0.9), {"c1": 5},
        min_alert_confidence=0.6, recurring_threshold=3,
        impact=impact,
    )
    assert action == "alert"
    assert decision.alert is True


def test_high_impact_low_confidence_escalates_to_human_approval():
    fb = Feedback(problems=[])
    claims = [Claim(id="c1", text="damaged item", kind="problem", severity="high")]
    verifications = [_verified("c1", status="unsupported", span_found=True, supported=False)]
    impact = ImpactScore(score=0.85, label="high")
    decision, action, dissent = policy.evaluate(
        fb, claims, verifications, _conf(total=0.2), {"c1": 1},
        min_alert_confidence=0.6, recurring_threshold=3,
        impact=impact, proposed_alert=True,
    )
    assert action == "human_approval"
    assert decision.alert is False
    assert any(d.source_agent == "verification" for d in dissent)


def test_low_impact_does_not_alert_problem():
    fb = Feedback(problems=[])
    claims = [Claim(id="c1", text="cold fries", kind="problem", severity="high")]
    verifications = [_verified("c1")]
    impact = ImpactScore(score=0.3, label="low")
    decision, action, _ = policy.evaluate(
        fb, claims, verifications, _conf(total=0.9), {"c1": 5},
        min_alert_confidence=0.6, recurring_threshold=3,
        impact=impact,
    )
    # Recurring but low impact -> record, not alert.
    assert action == "record"
    assert decision.alert is False

