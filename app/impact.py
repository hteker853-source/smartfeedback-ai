"""Deterministic business-impact score.

Sentiment is not the product metric. This module converts evidence into an
operational impact score that answers "how much does this recurring problem
actually cost the business?" — computed purely from deterministic signals:
recurrence, severity, affected customers, trend and recency. Never an LLM guess.
"""

from __future__ import annotations

import math

from .schemas import ImpactScore

_SEVERITY_WEIGHT = {"low": 0.2, "medium": 0.5, "high": 0.8, "critical": 1.0}

_TREND_WEIGHT = {
    "rising": 1.0,
    "increasing": 1.0,
    "new": 0.7,
    "stable": 0.5,
    "improving": 0.2,
    "decreasing": 0.2,
    "unknown": 0.4,
}

# log2 denominator that saturates recurrence/affected-customer contributions at
# ~8 occurrences / ~8 affected customers (so "8" is already "fully impactful").
_SATURATION = math.log2(8.0)


def _saturate(count: float) -> float:
    if count is None or count <= 0:
        return 0.0
    return min(1.0, math.log2(1.0 + count) / _SATURATION)


def _recency_weight(last_seen_days_ago: float | None) -> float:
    if last_seen_days_ago is None:
        return 0.5
    if last_seen_days_ago <= 1:
        return 1.0
    if last_seen_days_ago <= 3:
        return 0.8
    if last_seen_days_ago <= 7:
        return 0.6
    if last_seen_days_ago <= 14:
        return 0.4
    return 0.2


def compute_impact(
    *,
    occurrence_count: int,
    severity: str,
    affected_customers: int = 1,
    trend_direction: str = "unknown",
    last_seen_days_ago: float | None = None,
) -> ImpactScore:
    recurrence = _saturate(occurrence_count)
    severity_w = _SEVERITY_WEIGHT.get(severity, 0.5)
    affected = _saturate(affected_customers)
    trend = _TREND_WEIGHT.get(trend_direction, 0.4)
    recency = _recency_weight(last_seen_days_ago)

    score = round(
        0.30 * recurrence
        + 0.30 * severity_w
        + 0.15 * affected
        + 0.15 * trend
        + 0.10 * recency,
        3,
    )
    if score >= 0.7:
        label = "high"
    elif score >= 0.45:
        label = "medium"
    else:
        label = "low"

    return ImpactScore(
        score=score,
        label=label,
        recurrence=round(recurrence, 3),
        severity=round(severity_w, 3),
        affected_customers=round(affected, 3),
        trend=round(trend, 3),
        recency=round(recency, 3),
    )
