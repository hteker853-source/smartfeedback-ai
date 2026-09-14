"""Tests for the offline evaluation harness."""

from app.eval_harness import DEFAULT_GOLDEN, run_evaluation


def test_evaluation_runs_and_reports_structure():
    report = run_evaluation()
    assert report["n_samples"] == len(DEFAULT_GOLDEN)
    assert "escalation" in report
    assert "missing_items" in report
    assert "dnc" in report
    assert "calibration" in report
    assert set(report["escalation"]) == {"tp", "fp", "fn", "tn", "precision", "recall", "f1", "accuracy"}
    # Sentiment accuracy must be in [0, 1].
    assert 0.0 <= report["sentiment_accuracy"] <= 1.0


def test_evaluation_is_deterministic():
    a = run_evaluation()
    b = run_evaluation()
    assert a["sentiment_accuracy"] == b["sentiment_accuracy"]
    assert a["escalation"] == b["escalation"]
    assert a["missing_items"] == b["missing_items"]
    assert a["dnc"] == b["dnc"]


def test_golden_baseline_no_false_alarms():
    report = run_evaluation()
    # Escalation recall must be perfect: no real missing item is ever missed.
    assert report["escalation"]["recall"] == 1.0
    # No false alarms on the hand-crafted golden set.
    assert report["escalation"]["fp"] == 0
    # DNC detection is robust on explicit phrases.
    assert report["dnc"]["recall"] == 1.0
    assert report["dnc"]["fp"] == 0
