"""Tests for human-approval hardening and notification idempotency."""


def _biz(repo):
    return repo.get_or_create_business("Test Business")


def test_resolve_action_is_idempotent(repo):
    biz = _biz(repo)
    action = repo.create_action(biz["id"], "compensation_offer", proposed_by="agent")
    assert repo.resolve_action(action["id"], "approved:50%") is True
    # Second resolve (duplicate callback) must NOT re-apply.
    assert repo.resolve_action(action["id"], "approved:75%") is False
    a = repo.get_action(action["id"])
    assert a["status"] == "resolved"
    assert a["decision"] == "approved:50%"


def test_create_action_sets_expiry(repo):
    biz = _biz(repo)
    action = repo.create_action(biz["id"], "compensation_offer", proposed_by="agent")
    assert action.get("expires_at") is not None


def test_notification_dedup_by_idempotency_key(repo):
    assert repo.record_notification(None, "alarm", "alarm:1") is True
    assert repo.record_notification(None, "alarm", "alarm:1") is False
    # A different key is a different notification.
    assert repo.record_notification(None, "alarm", "alarm:2") is True
