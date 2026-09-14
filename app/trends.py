"""Deterministic trend / problem-resolution classification.

All arithmetic is done here in Python (never in the LLM). Percentages guard
against zero-division and avoid nonsensical outputs like "%500 increase" when
the previous period was empty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

Direction = Literal["new", "increasing", "decreasing", "stable", "unknown"]


def percent_change(prev: float, cur: float) -> float | None:
    """Percent change from prev to cur. Returns None when there is no baseline."""
    if prev == 0:
        if cur == 0:
            return 0.0
        return None
    return round((cur - prev) / prev * 100.0, 1)


def classify_problem(
    total: int,
    recent: int,
    prev: int,
    last_seen_days_ago: float | None,
    *,
    min_data: int = 2,
    resolved_quiet_days: float = 7.0,
) -> tuple[str, Direction]:
    """Classify a canonical problem's status and direction.

    `total`  = all-time mention count.
    `recent` = mentions in the current window.
    `prev`   = mentions in the previous window.
    `last_seen_days_ago` = days since the last mention (or None if unknown).
    """
    if total <= 0 or total < min_data:
        return "insufficient_data", "unknown"

    if prev == 0 and recent > 0:
        direction: Direction = "new"
    elif recent < prev:
        direction = "decreasing"
    elif recent > prev:
        direction = "increasing"
    else:
        direction = "stable"

    # Resolution requires a sustained quiet period, never a single good day.
    if recent == 0 and prev == 0:
        if last_seen_days_ago is not None and last_seen_days_ago >= resolved_quiet_days:
            return "likely_resolved", "stable"
        return "improving", "decreasing"

    if recent == 0 and prev > 0:
        return "improving", "decreasing"

    if direction == "decreasing":
        return "improving", direction
    return "active", direction


class TrendReport:
    """Deterministic trend analysis with confidence and data sufficiency.

    A "trend" is only claimed when there is enough data; otherwise the honest
    result is INSUFFICIENT_DATA — never a fabricated "rising" from two samples.
    """

    def __init__(
        self,
        status: str,
        direction: Direction,
        trend_confidence: float,
        data_sufficiency: str,
        sample_size: int,
        recent: int,
        prev: int,
    ) -> None:
        self.status = status
        self.direction = direction
        self.trend_confidence = trend_confidence
        self.data_sufficiency = data_sufficiency
        self.sample_size = sample_size
        self.recent = recent
        self.prev = prev

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "direction": self.direction,
            "trend_confidence": self.trend_confidence,
            "data_sufficiency": self.data_sufficiency,
            "sample_size": self.sample_size,
            "recent": self.recent,
            "prev": self.prev,
        }


def classify_problem_rich(
    total: int,
    recent: int,
    prev: int,
    last_seen_days_ago: float | None,
    *,
    min_data: int = 2,
    resolved_quiet_days: float = 7.0,
) -> TrendReport:
    """Rich trend classification with trend_confidence and data_sufficiency."""
    status, direction = classify_problem(
        total, recent, prev, last_seen_days_ago,
        min_data=min_data, resolved_quiet_days=resolved_quiet_days,
    )

    sample_size = total
    if sample_size < min_data or status == "insufficient_data":
        return TrendReport(
            status=status, direction=direction, trend_confidence=0.0,
            data_sufficiency="insufficient", sample_size=sample_size,
            recent=recent, prev=prev,
        )

    # More samples -> more confidence in the direction (diminishing returns).
    base = min(1.0, 0.4 + 0.1 * sample_size)
    if status == "active" and direction == "increasing":
        base += 0.1
    elif status == "likely_resolved":
        base = min(1.0, base + 0.2)

    sufficiency = "sufficient" if sample_size >= min_data else "insufficient"
    return TrendReport(
        status=status, direction=direction,
        trend_confidence=round(min(1.0, base), 3),
        data_sufficiency=sufficiency,
        sample_size=sample_size, recent=recent, prev=prev,
    )


SatisfactionTrend = Literal["improving", "worsening", "stable", "insufficient_data"]

# Below this absolute percent change, a fluctuation is treated as noise, not
# a real trend — mirrors classify_problem's "never over-claim from thin
# data" philosophy, applied to a continuous (not count-based) metric.
_STABLE_BAND_PCT = 5.0


def classify_satisfaction_trend(
    prev_avg: float | None,
    recent_avg: float | None,
    prev_n: int,
    recent_n: int,
    *,
    min_samples: int = 3,
) -> tuple[SatisfactionTrend, float | None]:
    """Classify a satisfaction-score trend between two comparable windows.

    Used by GÖREV C (foreign-language-customer satisfaction trend), built on
    the same "never fabricate a trend from too little data" principle as
    `classify_problem`/`classify_problem_rich`: below `min_samples` in either
    window, the honest answer is "insufficient_data", never a guessed number.

    Returns (label, percent_change). `percent_change` is None whenever the
    label is "insufficient_data" (no reliable number to report).
    """
    if (
        recent_n < min_samples
        or prev_n < min_samples
        or recent_avg is None
        or prev_avg is None
    ):
        return "insufficient_data", None

    change = percent_change(prev_avg, recent_avg)
    if change is None:
        return "insufficient_data", None
    if change >= _STABLE_BAND_PCT:
        return "improving", change
    if change <= -_STABLE_BAND_PCT:
        return "worsening", change
    return "stable", change


def compute_customer_rhythm(
    order_dates_desc: list[str],
    now: datetime | None = None,
    *,
    min_orders: int = 4,
) -> dict[str, Any]:
    """GÖREV F: a customer's "normal ordering rhythm", derived purely from
    their OWN order history — never a fixed/global day count.

    `order_dates_desc`: ISO timestamps of past orders (any order, most-recent
    first or not — this function sorts them). Requires at least `min_orders`
    orders (so at least `min_orders - 1` intervals) to compute a reliable
    average; below that, the honest answer is "insufficient_data", matching
    `classify_problem`'s "never fabricate from too little data" principle.

    Returns a dict with:
      - status: "ok" | "insufficient_data"
      - avg_interval_days: the customer's own historical average gap between
        orders (None if insufficient_data)
      - days_since_last: days since their most recent order
      - ratio: days_since_last / avg_interval_days (how many multiples of
        their normal rhythm have elapsed) — the caller compares this against
        a configurable threshold (never a hardcoded day count).
    """
    if len(order_dates_desc) < min_orders:
        return {
            "status": "insufficient_data",
            "avg_interval_days": None,
            "days_since_last": None,
            "ratio": None,
            "sample_size": len(order_dates_desc),
        }

    now = now or datetime.now(timezone.utc)

    def _parse(iso: str) -> datetime:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    parsed = sorted((_parse(d) for d in order_dates_desc), reverse=True)
    intervals = [
        (parsed[i] - parsed[i + 1]).total_seconds() / 86400.0
        for i in range(len(parsed) - 1)
    ]
    avg_interval = sum(intervals) / len(intervals)
    days_since_last = (now - parsed[0]).total_seconds() / 86400.0
    ratio = (days_since_last / avg_interval) if avg_interval > 0 else None

    return {
        "status": "ok",
        "avg_interval_days": round(avg_interval, 1),
        "days_since_last": round(days_since_last, 1),
        "ratio": round(ratio, 2) if ratio is not None else None,
        "sample_size": len(order_dates_desc),
    }

