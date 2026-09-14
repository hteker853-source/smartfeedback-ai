import asyncio
from datetime import datetime, timedelta, timezone

from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def _old_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def test_purge_keeps_structured(repo, settings):
    p = Pipeline(repo, settings)
    # Create a feedback and then age it beyond retention.
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], "Yemek çok güzeldi, teşekkürler.", simulated=True))

    # Age the feedback/call rows and purge.
    cutoff = _old_iso(-1)  # in the future -> nothing purged
    with _open(repo.db_path) as conn:
        conn.execute("UPDATE feedbacks SET created_at = ?", (_old_iso(100),))
        conn.execute("UPDATE calls SET created_at = ?", (_old_iso(100),))

    n = repo.purge_raw_transcripts(_old_iso(90))
    assert n >= 1

    fb = repo.list_feedbacks(p.business_id)[0]
    assert fb["transcript"] is None            # raw transcript purged
    assert fb["satisfaction"] is not None      # structured kept
    assert fb["sentiment"] is not None         # structured kept


def test_purge_does_not_touch_recent(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], "Harika.", simulated=True))
    n = repo.purge_raw_transcripts(_old_iso(90))  # cutoff 90 days ago -> recent kept
    assert n == 0
    fb = repo.list_feedbacks(p.business_id)[0]
    assert fb["transcript"] is not None


def _open(db_path):
    from app.db import connect

    return connect(db_path)
