"""Seed demo data.

Creates a realistic history (customers, orders, feedbacks, canonical problems)
that demonstrates recurring-problem detection and improvement trends. Safe to
run repeatedly: it exits early if data already exists.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from .analyzer import analyze_feedback
from .canonical import CanonicalMatcher
from .config import get_settings
from .db import init_db
from .repository import Repository

SEED_TRANSCRIPTS = [
    # (days_ago, transcript)
    (13, "Yemek güzeldi ama patatesler soğuk geldi."),
    (11, "Patatesler sıcak değildi, çok üzüldüm."),
    (9, "Yemek harikaydı, her şey çok lezzetli."),
    (8, "Patates kızartması soğuktu."),
    (6, "Çok memnun kaldım, teşekkürler."),
    (5, "Patatesler yine soğuk gelmiş."),
    (4, "Ayran sipariş etmiştik ama gelmedi."),
    (3, "Her şey mükemmeldi, teşekkürler."),
    (2, "Patatesler sıcaktı, çok beğendim."),
    (1, "Yemek lezzetli, teslimat hızlıydı."),
]


def seed(settings=None, repo: Repository | None = None) -> int:
    settings = settings or get_settings()
    init_db(settings.database_file)
    if repo is None:
        repo = Repository(settings.database_file)
    business = repo.get_or_create_business(settings.business_name)

    existing = repo.list_feedbacks(business["id"], limit=1)
    if existing:
        return 0

    matcher = CanonicalMatcher(repo, settings)
    phone_base = 5550000000
    for i, (days_ago, transcript) in enumerate(SEED_TRANSCRIPTS):
        phone = f"+90{phone_base + i}"
        customer = repo.upsert_customer(business["id"], phone)
        now = datetime.now(timezone.utc) - timedelta(days=days_ago)
        iso = now.isoformat(timespec="seconds")
        order = repo.create_order(business["id"], customer["id"], "completed")
        with _conn(settings.database_file) as conn:
            conn.execute(
                "UPDATE orders SET status='completed', sent_at=?, completed_at=? WHERE id=?",
                (iso, iso, order["id"]),
            )
        call = repo.create_call(
            order["id"], customer["id"], status="completed", scheduled_at=iso
        )
        repo.update_call(
            call["id"], completed_at=iso, transcript=transcript, outcome="transcribed",
        )
        feedback = analyze_feedback(transcript, "tr")
        saved = repo.save_feedback(
            call_id=call["id"],
            order_id=order["id"],
            customer_id=customer["id"],
            business_id=business["id"],
            feedback=feedback,
            transcript=transcript,
        )
        problem_items = [(p.text, p.severity) for p in feedback.problems]
        for m in feedback.missing_products:
            problem_items.append((f"missing {m.name}", "high"))
        for text, severity in problem_items:
            canon, _ = matcher.resolve(business["id"], text, iso)
            repo.link_feedback_problem(saved["id"], canon["id"], text, severity)
        repo.update_customer_stats(customer["id"])

    return len(SEED_TRANSCRIPTS)


def _conn(db_path):
    from .db import connect

    return connect(db_path)


# ---------------------------------------------------------------------------
# Recurring-case demo seed (clearly marked DEMO DATA, idempotent).
#
# Produces a single recurring operational case — "missing ayran" — with
# 7 occurrences across 6 unique demo customers, a RISING trend and HIGH impact,
# WITHOUT any live CALL-E call. The 6 backdated occurrences build the history;
# the 7th runs the real pipeline (simulated) so the decision envelope + case are
# produced through the actual code path. Demo customers use the dedicated
# +90 555 999 xxxx block and order_type="demo".
# ---------------------------------------------------------------------------
DEMO_PROBLEM = "missing ayran"
DEMO_PHONES = [f"+90555999000{i}" for i in range(1, 7)]  # 6 demo customers
# (days_ago, customer_index) — 2 in the previous window (8-14d), 4 recent (0-7d).
DEMO_HISTORY = [
    (13, 0), (10, 1),
    (6, 2), (5, 3), (4, 4), (2, 5),
]
DEMO_TRANSCRIPT = "Ayran sipariş etmiştik ama gelmedi."


def seed_recurring_case(settings=None, repo: Repository | None = None) -> dict:
    """Seed a recurring demo case. Idempotent: returns early if already seeded."""
    import asyncio

    settings = settings or get_settings()
    init_db(settings.database_file)
    if repo is None:
        repo = Repository(settings.database_file)
    business = repo.get_or_create_business(settings.business_name)

    if repo.get_customer(business["id"], DEMO_PHONES[0]) is not None:
        return _demo_summary(repo, business["id"])

    matcher = CanonicalMatcher(repo, settings)
    for days_ago, cust_idx in DEMO_HISTORY:
        phone = DEMO_PHONES[cust_idx]
        customer = repo.upsert_customer(business["id"], phone)
        now = datetime.now(timezone.utc) - timedelta(days=days_ago)
        iso = now.isoformat(timespec="seconds")
        order = repo.create_order(
            business["id"], customer["id"], "completed", order_type="demo"
        )
        with _conn(settings.database_file) as conn:
            conn.execute(
                "UPDATE orders SET status='completed', sent_at=?, completed_at=? WHERE id=?",
                (iso, iso, order["id"]),
            )
        call = repo.create_call(
            order["id"], customer["id"], status="completed", scheduled_at=iso
        )
        repo.update_call(
            call["id"], completed_at=iso, transcript=DEMO_TRANSCRIPT,
            outcome="transcribed", is_simulated=1,
        )
        feedback = analyze_feedback(DEMO_TRANSCRIPT, "tr")
        saved = repo.save_feedback(
            call_id=call["id"], order_id=order["id"], customer_id=customer["id"],
            business_id=business["id"], feedback=feedback, transcript=DEMO_TRANSCRIPT,
        )
        with _conn(settings.database_file) as conn:
            conn.execute(
                "UPDATE feedbacks SET created_at=? WHERE id=?", (iso, saved["id"])
            )
        canon, _ = matcher.resolve(business["id"], DEMO_PROBLEM, iso)
        repo.link_feedback_problem(saved["id"], canon["id"], DEMO_PROBLEM, "high")
        # Fold the occurrence into the case so occurrence_count reflects history.
        repo.upsert_case(
            business["id"], canon["id"], DEMO_PROBLEM, severity="high", confidence=0.6,
            evidence_json=None, decision="alert", dissent_json=None,
            last_decision_action="alert",
        )
        repo.update_customer_stats(customer["id"])

    # 7th occurrence: run the real pipeline (simulated) through a repeat customer,
    # so the final decision envelope + case impact/trend are computed for real.
    from .pipeline import Pipeline

    pipeline = Pipeline(repo, settings)
    asyncio.run(
        pipeline.simulate_call(DEMO_PHONES[2], DEMO_TRANSCRIPT, notify_status=False)
    )
    return _demo_summary(repo, business["id"])


def _demo_summary(repo: Repository, business_id: int) -> dict:
    canon = repo.get_canonical_problem(business_id, DEMO_PROBLEM)
    if canon is None:
        return {"status": "empty", "problem": DEMO_PROBLEM}
    case = None
    for c in repo.list_cases(business_id):
        if c.get("title") == DEMO_PROBLEM or c.get("canonical_problem_id") == canon["id"]:
            case = c
            break
    return {
        "status": "seeded",
        "problem": DEMO_PROBLEM,
        "occurrence_count": case["occurrence_count"] if case else canon["count"],
        "affected_customer_count": case["affected_customer_count"] if case else 0,
        "trend_direction": case["trend_direction"] if case else None,
        "impact_score": case["impact_score"] if case else 0.0,
        "case_id": case["id"] if case else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--recurring-case", action="store_true",
        help="Seed a recurring demo case (7 occurrences / 6 customers, clearly DEMO data).",
    )
    args = parser.parse_args()
    if args.recurring_case:
        summary = seed_recurring_case()
        print("Recurring demo case:", summary)
        return
    n = seed()
    print(f"Seeded {n} feedbacks." if n else "Seed skipped (data exists).")


if __name__ == "__main__":
    main()
