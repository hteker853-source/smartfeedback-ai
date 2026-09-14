from app.trends import classify_problem, classify_problem_rich, percent_change


def test_percent_change_zero_division_guard():
    assert percent_change(0, 0) == 0.0
    assert percent_change(0, 5) is None  # no baseline -> no "%500 increase"
    assert percent_change(10, 15) == 50.0
    assert percent_change(10, 5) == -50.0


def test_classify_new():
    status, direction = classify_problem(total=3, recent=3, prev=0, last_seen_days_ago=0)
    assert status == "active"
    assert direction == "new"


def test_classify_improving():
    status, direction = classify_problem(total=10, recent=1, prev=5, last_seen_days_ago=1)
    assert status == "improving"
    assert direction == "decreasing"


def test_classify_resolved_requires_sustained_quiet():
    # recent 0, prev 0, but only quiet 1 day -> NOT resolved
    status, _ = classify_problem(total=20, recent=0, prev=0, last_seen_days_ago=1)
    assert status == "improving"
    # quiet for 10 days -> resolved
    status, _ = classify_problem(total=20, recent=0, prev=0, last_seen_days_ago=10)
    assert status == "likely_resolved"


def test_classify_single_good_day_never_resolves():
    # Still recent mentions -> not resolved even if dropping
    status, _ = classify_problem(total=20, recent=2, prev=3, last_seen_days_ago=0)
    assert status != "likely_resolved"


def test_classify_insufficient_data():
    status, _ = classify_problem(total=1, recent=1, prev=0, last_seen_days_ago=0)
    assert status == "insufficient_data"
    status, _ = classify_problem(total=0, recent=0, prev=0, last_seen_days_ago=None)
    assert status == "insufficient_data"


def test_classify_rising():
    status, direction = classify_problem(total=10, recent=8, prev=2, last_seen_days_ago=0)
    assert status == "active"
    assert direction == "increasing"


def test_rich_trend_insufficient_data():
    r = classify_problem_rich(total=1, recent=1, prev=0, last_seen_days_ago=0)
    assert r.data_sufficiency == "insufficient"
    assert r.trend_confidence == 0.0
    assert r.status == "insufficient_data"


def test_rich_trend_sufficient_has_confidence():
    r = classify_problem_rich(total=10, recent=8, prev=2, last_seen_days_ago=0)
    assert r.data_sufficiency == "sufficient"
    assert 0.0 < r.trend_confidence <= 1.0
    assert r.direction == "increasing"


def test_rich_trend_two_samples_is_not_strong_rising():
    # Two samples is enough to register "new", but never a confident "rising".
    r = classify_problem_rich(total=2, recent=2, prev=0, last_seen_days_ago=0)
    assert r.status == "active"
    assert r.direction == "new"  # not "increasing"
    assert r.trend_confidence < 0.8

