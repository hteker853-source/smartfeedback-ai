"""Tests for the deterministic business-impact score."""

from app.impact import compute_impact


def test_high_impact_from_recurring_critical_rising():
    impact = compute_impact(
        occurrence_count=8,
        severity="critical",
        affected_customers=6,
        trend_direction="rising",
        last_seen_days_ago=1,
    )
    assert impact.label == "high"
    assert impact.score >= 0.7


def test_low_impact_from_single_low_severity():
    impact = compute_impact(
        occurrence_count=1,
        severity="low",
        affected_customers=1,
        trend_direction="unknown",
        last_seen_days_ago=None,
    )
    assert impact.label == "low"
    assert impact.score < 0.45


def test_impact_monotonic_in_occurrence():
    a = compute_impact(occurrence_count=1, severity="medium", trend_direction="stable", last_seen_days_ago=2)
    b = compute_impact(occurrence_count=8, severity="medium", trend_direction="stable", last_seen_days_ago=2)
    assert b.score > a.score
    assert b.recurrence > a.recurrence


def test_impact_components_in_bounds():
    impact = compute_impact(
        occurrence_count=3,
        severity="high",
        affected_customers=4,
        trend_direction="rising",
        last_seen_days_ago=0,
    )
    for comp in ("recurrence", "severity", "affected_customers", "trend", "recency"):
        assert 0.0 <= getattr(impact, comp) <= 1.0
    assert 0.0 <= impact.score <= 1.0
