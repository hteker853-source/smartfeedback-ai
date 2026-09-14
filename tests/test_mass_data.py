import time

from app.analyzer import analyze_feedback
from app.canonical import CanonicalMatcher
from app.pipeline import Pipeline
from app.repository import Repository

TRANSCRIPTS = [
    "Yemek güzeldi ama patatesler soğuk geldi.",
    "Her şey harikaydı, teşekkürler.",
    "Ayran sipariş etmiştik ama gelmedi.",
    "Teslimat çok geç oldu, bekledim.",
    "Yemek lezzetliydi, çok memnun kaldım.",
    "Patatesler sıcak değildi.",
    "Kurye çok kibardı, teşekkürler.",
    "Pizza soğuktu, üzüldüm.",
    "Garnitür miktarı azdı.",
    "Sorunsuz teslimat, teşekkürler.",
]


def test_mass_data_aggregation(repo, settings):
    p = Pipeline(repo, settings)
    matcher = CanonicalMatcher(repo, settings)
    biz = p.business_id

    n = 1000
    start = time.time()
    for i in range(n):
        phone = f"+90555{i:07d}"
        customer = repo.upsert_customer(biz, phone)
        order = repo.create_order(biz, customer["id"], "completed")
        call = repo.create_call(order["id"], customer["id"], status="completed")
        transcript = TRANSCRIPTS[i % len(TRANSCRIPTS)]
        feedback = analyze_feedback(transcript, "tr")
        saved = repo.save_feedback(
            call_id=call["id"], order_id=order["id"], customer_id=customer["id"],
            business_id=biz, feedback=feedback, transcript=transcript,
        )
        for text, sev in [(pr.text, pr.severity) for pr in feedback.problems]:
            canon, _ = matcher.resolve(biz, text, saved["created_at"])
            repo.link_feedback_problem(saved["id"], canon["id"], text, sev)
        repo.update_customer_stats(customer["id"])
    elapsed = time.time() - start

    assert len(repo.list_feedbacks(biz, limit=2000)) == 1000
    assert elapsed < 60  # sanity bound for the CI environment

    # Aggregation / synthesis must not crash and must be idempotent.
    insights = _run(p.nightly_synthesis())
    assert len(insights) == 1
    insights2 = _run(p.nightly_synthesis())
    assert len(insights2) == 0  # idempotent


def _run(coro):
    import asyncio

    return asyncio.run(coro)
