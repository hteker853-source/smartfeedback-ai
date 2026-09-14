import asyncio

from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def _make_loyal_customer(repo, p, phone="+905551112233"):
    """Create a customer with >=10 orders -> loyalty_status 'loyal'."""
    customer = repo.upsert_customer(p.business_id, phone)
    for _ in range(10):
        repo.create_order(p.business_id, customer["id"], "completed")
    repo.update_customer_stats(customer["id"])
    return repo.get_customer(p.business_id, phone)


def test_negative_loyal_customer_proposes_compensation(repo, settings):
    p = Pipeline(repo, settings)
    _make_loyal_customer(repo, p)
    # negative sentiment, no missing product -> compensation proposal
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], "Yemek çok soğuktu, memnun kalmadım.", simulated=True))

    actions = _list_actions(repo, p.business_id)
    assert any(a["action_type"] == "compensation_offer" and a["status"] == "proposed" for a in actions)


def test_approval_records_but_never_auto_applies(repo, settings):
    p = Pipeline(repo, settings)
    _make_loyal_customer(repo, p)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], "Yemek soğuktu, kötüydü.", simulated=True))

    actions = _list_actions(repo, p.business_id)
    offer = [a for a in actions if a["action_type"] == "compensation_offer"][0]

    # Capture DB state before "approval".
    orders_before = _count(repo, "orders")
    calls_before = _count(repo, "calls")
    customers_before = repo.get_customer(p.business_id, "+905551112233")

    # Approve 50%.
    repo.resolve_action(offer["id"], "approved:50%")

    # Approval must only record the decision; it must NOT change any customer/order/call data.
    assert _count(repo, "orders") == orders_before
    assert _count(repo, "calls") == calls_before
    customers_after = repo.get_customer(p.business_id, "+905551112233")
    assert customers_after["order_count"] == customers_before["order_count"]

    action = _get_action(repo, offer["id"])
    assert action["status"] == "resolved"
    assert action["decision"] == "approved:50%"


def test_happy_customer_no_proposal(repo, settings):
    p = Pipeline(repo, settings)
    _make_loyal_customer(repo, p)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], "Her şey harikaydı, teşekkürler.", simulated=True))
    actions = _list_actions(repo, p.business_id)
    assert not any(a["action_type"] == "compensation_offer" for a in actions)


def _list_actions(repo, business_id):
    with _open(repo.db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM actions WHERE business_id=?", (business_id,)
        )]


def _get_action(repo, action_id):
    with _open(repo.db_path) as conn:
        return dict(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())


def _count(repo, table):
    with _open(repo.db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


def _open(db_path):
    from app.db import connect

    return connect(db_path)
