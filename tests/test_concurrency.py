import asyncio
import threading

from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def _worker(repo, pipeline, call_id, results, idx):
    results[idx] = _run(
        pipeline.process_call_result(call_id, "Yemek çok güzeldi.", simulated=True)
    )


def test_concurrent_process_call_result_single_feedback(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")

    n = 8
    threads = []
    results = [None] * n
    for i in range(n):
        t = threading.Thread(target=_worker, args=(repo, p, call["id"], results, i))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    succeeded = [r for r in results if r is not None]
    assert len(succeeded) == 1
    assert len(repo.list_feedbacks(p.business_id)) == 1


def test_concurrent_dispatch_single_call(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    order = result["order"]

    n = 8
    threads = []
    results = [None] * n

    def worker(idx):
        results[idx] = _run(p._dispatch_call(order))

    for i in range(n):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    created = [r for r in results if r is not None]
    assert len(created) == 1

    with _open(repo.db_path) as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) c FROM calls WHERE order_id=?", (order["id"],)
        ).fetchone()["c"]
    assert cnt == 1


def _open(db_path):
    from app.db import connect

    return connect(db_path)
