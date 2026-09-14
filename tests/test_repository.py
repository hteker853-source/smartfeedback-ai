from app.repository import Repository


def test_business_created_once(repo: Repository):
    b1 = repo.get_or_create_business("Test Business")
    b2 = repo.get_or_create_business("Test Business")
    assert b1["id"] == b2["id"]


def test_customer_upsert_and_stats(repo: Repository):
    b = repo.get_or_create_business("Test Business")
    c = repo.upsert_customer(b["id"], "+905551112233")
    assert repo.get_customer(b["id"], "+905551112233")["id"] == c["id"]

    for _ in range(3):
        repo.create_order(b["id"], c["id"], "completed")
    repo.update_customer_stats(c["id"])
    updated = repo.get_customer(b["id"], "+905551112233")
    assert updated["order_count"] == 3
    assert updated["loyalty_status"] == "regular"


def test_do_not_call(repo: Repository):
    b = repo.get_or_create_business("Test Business")
    c = repo.upsert_customer(b["id"], "+905551112233")
    assert repo.is_do_not_call(c["id"]) is False
    repo.set_do_not_call(c["id"])
    assert repo.is_do_not_call(c["id"]) is True


def test_order_and_call_roundtrip(repo: Repository):
    b = repo.get_or_create_business("Test Business")
    c = repo.upsert_customer(b["id"], "+905551112233")
    order = repo.create_order(b["id"], c["id"], "created")
    sent = repo.mark_order_sent(order["id"], 30)
    assert sent["status"] == "sent"
    assert sent["feedback_scheduled_at"] is not None

    call = repo.create_call(order["id"], c["id"], status="calling")
    assert repo.get_call(call["id"])["order_id"] == order["id"]


def test_one_call_per_order_enforced(repo: Repository):
    import sqlite3

    import pytest

    b = repo.get_or_create_business("Test Business")
    c = repo.upsert_customer(b["id"], "+905551112233")
    order = repo.create_order(b["id"], c["id"], "sent")
    repo.create_call(order["id"], c["id"])
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_call(order["id"], c["id"])


def test_feedback_saved(repo: Repository):
    from app.schemas import Feedback

    b = repo.get_or_create_business("Test Business")
    c = repo.upsert_customer(b["id"], "+905551112233")
    order = repo.create_order(b["id"], c["id"], "sent")
    call = repo.create_call(order["id"], c["id"])
    fb = Feedback(satisfaction=4, sentiment="positive", positives=["taste"])
    saved = repo.save_feedback(
        call_id=call["id"], order_id=order["id"], customer_id=c["id"],
        business_id=b["id"], feedback=fb, transcript="great",
    )
    assert saved["satisfaction"] == 4
    assert len(repo.list_feedbacks(b["id"])) == 1
