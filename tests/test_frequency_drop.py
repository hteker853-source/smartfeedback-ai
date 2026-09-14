"""GÖREV F: frequency-drop risk detection — independent from GÖREV 2's
dormant-customer check, reuses the GÖREV 3 compensation_offer flow verbatim."""

import asyncio
from datetime import datetime, timedelta, timezone

from app import notify
from app.db import connect as _connect
from app.pipeline import Pipeline
from app.trends import compute_customer_rhythm


def _run(coro):
    return asyncio.run(coro)


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _make_customer_with_orders(repo, business_id, phone, order_days_ago: list[float]):
    """Create a customer with orders backdated to the given days-ago offsets."""
    customer = repo.upsert_customer(business_id, phone)
    for days_ago in order_days_ago:
        order = repo.create_order(business_id, customer["id"], "completed")
        with _connect(repo.db_path) as conn:
            conn.execute(
                "UPDATE orders SET created_at = ? WHERE id = ?",
                (_iso(days_ago), order["id"]),
            )
    with _connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE customers SET last_order_at = ? WHERE id = ?",
            (_iso(min(order_days_ago)), customer["id"]),
        )
    return repo.get_customer(business_id, phone)


# -- pure rhythm calculation ----------------------------------------------

def test_rhythm_insufficient_data_below_min_orders():
    result = compute_customer_rhythm(["2026-09-01T00:00:00+00:00"] * 2, min_orders=4)
    assert result["status"] == "insufficient_data"
    assert result["ratio"] is None


def test_rhythm_normal_ratio_from_regular_orders():
    now = datetime.now(timezone.utc)
    # Orders every 10 days, last one 10 days ago -> ratio ~1.0 (on schedule).
    dates = [(now - timedelta(days=10 * i)).isoformat() for i in range(1, 5)]
    result = compute_customer_rhythm(dates, now=now, min_orders=4)
    assert result["status"] == "ok"
    assert 9.5 <= result["avg_interval_days"] <= 10.5
    assert 0.9 <= result["ratio"] <= 1.1


def test_rhythm_elevated_ratio_when_customer_goes_quiet():
    now = datetime.now(timezone.utc)
    # Historical rhythm: every 10 days, but last order was 25 days ago.
    dates = [(now - timedelta(days=25)).isoformat()]
    dates += [(now - timedelta(days=25 + 10 * i)).isoformat() for i in range(1, 4)]
    result = compute_customer_rhythm(dates, now=now, min_orders=4)
    assert result["status"] == "ok"
    assert result["ratio"] >= 2.0  # ~2.5x their normal rhythm


# -- Pipeline.detect_frequency_drop_risk (end to end) ----------------------

def test_insufficient_history_never_proposed(repo, settings):
    p = Pipeline(repo, settings)
    _make_customer_with_orders(repo, p.business_id, "+905551110001", [1, 11])  # only 2 orders
    n = _run(p.detect_frequency_drop_risk())
    assert n == 0
    actions = _list_actions(repo, p.business_id)
    assert actions == []


def test_normal_rhythm_customer_not_flagged(repo, settings):
    p = Pipeline(repo, settings)
    # 5 orders, every ~10 days, most recent 10 days ago -> right on schedule.
    _make_customer_with_orders(
        repo, p.business_id, "+905551110002", [10, 20, 30, 40, 50]
    )
    n = _run(p.detect_frequency_drop_risk())
    assert n == 0
    assert _list_actions(repo, p.business_id) == []


def test_disrupted_rhythm_customer_proposes_compensation_offer(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    # Normal rhythm ~10 days, but last order was 30 days ago (~3x).
    customer = _make_customer_with_orders(
        repo, p.business_id, "+905551110003", [30, 40, 50, 60, 70]
    )

    captured = {}

    async def fake_send_approval(text, action_id):
        captured["text"] = text
        captured["action_id"] = action_id

    monkeypatch.setattr(notify, "_approval_sender", fake_send_approval)
    n = _run(p.detect_frequency_drop_risk())

    assert n == 1
    actions = _list_actions(repo, p.business_id)
    assert len(actions) == 1
    assert actions[0]["action_type"] == "compensation_offer"
    assert actions[0]["customer_id"] == customer["id"]
    assert "frequency_drop_risk" in actions[0]["reason"]
    # A real, distinct message (not the loyal-customer wording), mentioning the offer.
    assert "sipariş sıklığı düştü" in captured["text"]
    assert "indirim/tazminat teklifi" in captured["text"]


def test_idempotent_per_episode(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    _make_customer_with_orders(repo, p.business_id, "+905551110004", [30, 40, 50, 60, 70])
    monkeypatch.setattr(notify, "_approval_sender", lambda text, action_id: asyncio.sleep(0))

    first = _run(p.detect_frequency_drop_risk())
    second = _run(p.detect_frequency_drop_risk())
    assert first == 1
    assert second == 0  # already proposed for this episode -> not duplicated


def test_do_not_call_customers_are_excluded(repo, settings, monkeypatch):
    p = Pipeline(repo, settings)
    customer = _make_customer_with_orders(
        repo, p.business_id, "+905551110005", [30, 40, 50, 60, 70]
    )
    repo.set_do_not_call(customer["id"])
    monkeypatch.setattr(notify, "_approval_sender", lambda text, action_id: asyncio.sleep(0))
    n = _run(p.detect_frequency_drop_risk())
    assert n == 0


def test_independent_from_reengagement_outreach_same_customer_gets_two_requests(
    repo, settings, monkeypatch
):
    """A customer who is BOTH dormant-with-a-verified-complaint (GÖREV 2) AND
    frequency-drop-risk (GÖREV F) must trigger TWO separate approval
    requests, never merged into one."""
    from app.canonical import CanonicalMatcher
    from app.pipeline import DORMANT_DAYS
    from app.schemas import Feedback, Problem

    p = Pipeline(repo, settings)
    customer = _make_customer_with_orders(
        repo, p.business_id, "+905551110006",
        [DORMANT_DAYS + 5, DORMANT_DAYS + 15, DORMANT_DAYS + 25, DORMANT_DAYS + 35],
    )
    # Give this customer a verified complaint too (qualifies for GÖREV 2).
    # Backdated to the SAME old order so it doesn't introduce a fresh
    # "today" order that would reset their apparent rhythm/last-order-date.
    matcher = CanonicalMatcher(repo, p.settings)
    canon, _ = matcher.resolve(p.business_id, "cold food", "2026-01-01T00:00:00+00:00")
    order = repo.create_order(p.business_id, customer["id"], "completed")
    with _connect(repo.db_path) as conn:
        conn.execute(
            "UPDATE orders SET created_at = ? WHERE id = ?",
            (_iso(DORMANT_DAYS + 5), order["id"]),
        )
    call = repo.create_call(order["id"], customer["id"], status="completed")
    fb = Feedback(problems=[Problem(text="cold food", severity="high")], sentiment="negative")
    saved = repo.save_feedback(
        call_id=call["id"], order_id=order["id"], customer_id=customer["id"],
        business_id=p.business_id, feedback=fb, transcript="soğuktu",
    )
    repo.link_feedback_problem(saved["id"], canon["id"], "cold food", "high")
    repo.upsert_case(
        p.business_id, canon["id"], "cold food", severity="high", confidence=0.85,
        evidence_json=None, decision="alert", dissent_json=None, last_decision_action="alert",
    )

    import app.calle as calle_mod
    calle_mod.complete_json = lambda *a, **k: {"task": "merhaba, sizi özledik"}
    monkeypatch.setattr(calle_mod, "complete_json", lambda *a, **k: {"task": "merhaba, sizi özledik"})
    settings.deepseek_api_key = "fake-key"

    simple_calls = []
    approval_calls = []
    monkeypatch.setattr(notify, "_simple_approval_sender", lambda text, aid: simple_calls.append((text, aid)) or asyncio.sleep(0))
    monkeypatch.setattr(notify, "_approval_sender", lambda text, aid: approval_calls.append((text, aid)) or asyncio.sleep(0))

    n_reengage = _run(p.propose_reengagement_outreach())
    n_freq = _run(p.detect_frequency_drop_risk())

    assert n_reengage == 1
    assert n_freq == 1
    assert len(simple_calls) == 1  # reengagement_outreach approval
    assert len(approval_calls) == 1  # compensation_offer approval (frequency drop)

    actions = _list_actions(repo, p.business_id)
    types = sorted(a["action_type"] for a in actions)
    assert types == ["compensation_offer", "reengagement_outreach"]


def _list_actions(repo, business_id):
    with _connect(repo.db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM actions WHERE business_id=?", (business_id,)
        )]
