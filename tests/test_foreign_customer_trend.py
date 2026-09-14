"""GÖREV C: foreign-language-customer satisfaction trend — a real,
data-derived metric (never a fabricated/random one). Uses
customers.preferred_language (GÖREV 1) vs. BUSINESS_DEFAULT_LOCALE."""

from datetime import datetime, timedelta, timezone

from app.db import connect as _connect
from app.pipeline import Pipeline
from app.schemas import Feedback
from app.trends import classify_satisfaction_trend


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _seed_feedback(repo, business_id, phone, language, satisfaction, sentiment, days_ago):
    customer = repo.upsert_customer(business_id, phone)
    if language:
        repo.set_preferred_language(customer["id"], language)
    order = repo.create_order(business_id, customer["id"], "completed")
    call = repo.create_call(order["id"], customer["id"], status="completed")
    fb = Feedback(satisfaction=satisfaction, sentiment=sentiment)
    saved = repo.save_feedback(
        call_id=call["id"], order_id=order["id"], customer_id=customer["id"],
        business_id=business_id, feedback=fb, transcript="x",
    )
    with _connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE feedbacks SET created_at = ? WHERE id = ?", (_iso(days_ago), saved["id"]),
        )
    return saved


# -- pure classifier (trends.py) -----------------------------------------

def test_classify_insufficient_data_below_min_samples():
    label, change = classify_satisfaction_trend(4.0, 2.0, prev_n=2, recent_n=5)
    assert label == "insufficient_data"
    assert change is None


def test_classify_improving():
    label, change = classify_satisfaction_trend(3.0, 4.0, prev_n=3, recent_n=3)
    assert label == "improving"
    assert change > 0


def test_classify_worsening():
    label, change = classify_satisfaction_trend(4.0, 2.0, prev_n=3, recent_n=3)
    assert label == "worsening"
    assert change < 0


def test_classify_stable_within_noise_band():
    label, change = classify_satisfaction_trend(4.0, 4.1, prev_n=5, recent_n=5)
    assert label == "stable"


def test_classify_insufficient_when_no_baseline():
    label, change = classify_satisfaction_trend(None, 4.0, prev_n=5, recent_n=5)
    assert label == "insufficient_data"
    assert change is None


# -- end-to-end via Pipeline / real DB queries ----------------------------

def test_insufficient_data_with_no_foreign_customers(repo, settings):
    p = Pipeline(repo, settings)
    result = p.compute_foreign_customer_trend()
    assert result["trend"] == "insufficient_data"
    assert result["percent_change"] is None


def test_insufficient_data_below_three_samples(repo, settings):
    p = Pipeline(repo, settings)
    # Only 2 foreign-language feedbacks in the recent window -> insufficient.
    _seed_feedback(repo, p.business_id, "+905550000001", "en", 5, "positive", days_ago=1)
    _seed_feedback(repo, p.business_id, "+905550000002", "en", 4, "positive", days_ago=2)
    result = p.compute_foreign_customer_trend()
    assert result["trend"] == "insufficient_data"


def test_domestic_locale_customers_are_excluded(repo, settings):
    """settings.business_default_locale is 'tr' (conftest); customers whose
    preferred_language IS 'tr' must never count as 'foreign'."""
    p = Pipeline(repo, settings)
    for i in range(5):
        _seed_feedback(repo, p.business_id, f"+90555000000{i}", "tr", 5, "positive", days_ago=1)
    result = p.compute_foreign_customer_trend()
    assert result["trend"] == "insufficient_data"
    assert result["recent_n"] == 0


def test_real_improving_trend_computed_from_data(repo, settings):
    p = Pipeline(repo, settings)
    # Previous window (8-14 days ago): low satisfaction among English customers.
    for i in range(4):
        _seed_feedback(repo, p.business_id, f"+9055500001{i}", "en", 2, "negative", days_ago=10)
    # Recent window (0-7 days ago): satisfaction improved.
    for i in range(4):
        _seed_feedback(repo, p.business_id, f"+9055500002{i}", "en", 5, "positive", days_ago=2)

    result = p.compute_foreign_customer_trend(days=7)
    assert result["trend"] == "improving"
    assert result["percent_change"] > 0
    assert result["recent_n"] == 4
    assert result["prev_n"] == 4
    assert result["recent_avg_satisfaction"] == 5.0
    assert result["prev_avg_satisfaction"] == 2.0


def test_real_worsening_trend_computed_from_data(repo, settings):
    p = Pipeline(repo, settings)
    for i in range(3):
        _seed_feedback(repo, p.business_id, f"+9055500003{i}", "de", 5, "positive", days_ago=10)
    for i in range(3):
        _seed_feedback(repo, p.business_id, f"+9055500004{i}", "de", 1, "negative", days_ago=2)

    result = p.compute_foreign_customer_trend(days=7)
    assert result["trend"] == "worsening"
    assert result["percent_change"] < 0


def test_nightly_synthesis_includes_foreign_trend_line(repo, settings):
    import asyncio

    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    asyncio.run(p.process_call_result(call["id"], "Ayran gelmedi.", simulated=True))
    insights = asyncio.run(p.nightly_synthesis())
    assert len(insights) == 1
    assert "Farklı dil konuşan müşteri memnuniyeti" in insights[0]["description"]
