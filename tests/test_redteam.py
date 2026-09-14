"""Red-team: end-to-end idempotency and concurrency safety.

These verify the full side-effect chain (analysis -> DB -> case -> notification)
stays idempotent under duplicate webhooks and concurrent workers.
"""

import asyncio
import threading

from app.canonical import CanonicalMatcher
from app.db import connect
from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def test_duplicate_webhook_full_chain_idempotent(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    transcript = "Ayran sipariş etmiştik ama gelmedi."

    out1 = _run(p.process_call_result(call["id"], transcript, simulated=True))
    out2 = _run(p.process_call_result(call["id"], transcript, simulated=True))
    out3 = _run(p.process_call_result(call["id"], transcript, simulated=True))

    assert out1 is not None
    assert out2 is None and out3 is None

    assert len(repo.list_feedbacks(p.business_id)) == 1
    with connect(repo.db_path) as conn:
        n_notif = conn.execute("SELECT COUNT(*) c FROM notification_events").fetchone()["c"]
        n_insights = conn.execute("SELECT COUNT(*) c FROM insights").fetchone()["c"]
    assert n_notif == 1  # no duplicate alarm
    assert n_insights == 1  # no duplicate insight

    cases = repo.list_cases(p.business_id)
    assert len(cases) == 1
    assert cases[0]["occurrence_count"] == 1  # case not double-incremented


def test_concurrent_canonical_resolve_single_row(repo, settings):
    p = Pipeline(repo, settings)
    matcher = CanonicalMatcher(repo, settings)
    biz = p.business_id
    now = "2026-09-01T00:00:00+00:00"

    results = [None] * 8

    def worker(i):
        results[i] = matcher.resolve(biz, "patatesler soğuk geldi", now)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    problems = repo.list_canonical_problems(biz)
    # A single canonical row, and its count reflects all 8 concurrent mentions.
    assert len(problems) == 1
    assert problems[0]["count"] == 8
