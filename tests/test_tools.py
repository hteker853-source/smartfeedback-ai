import asyncio
import json

from app.canonical import CanonicalMatcher
from app.agents.tools import build_tools
from app.schemas import Feedback


def _run(coro):
    return asyncio.run(coro)


def _make_tools(repo, settings):
    biz = repo.get_or_create_business(settings.business_name)
    matcher = CanonicalMatcher(repo, settings)
    tools = build_tools(repo, biz["id"], matcher)
    return biz["id"], {t.tool_name: t for t in tools}


def test_seven_tools_registered(repo, settings):
    _, tools = _make_tools(repo, settings)
    expected = {
        "lookup_history_tool", "match_canonical_problem_tool", "save_feedback_tool",
        "send_telegram_alarm_tool", "update_dnc_list_tool", "record_agent_trace_tool",
        "propose_human_approval_tool",
    }
    assert set(tools) == expected


def test_lookup_history_tool(repo, settings):
    biz, tools = _make_tools(repo, settings)
    repo.upsert_customer(biz, "+905551112233")
    out = tools["lookup_history_tool"]("+905551112233")
    assert out["found"] is True
    assert "order_count" in out


def test_match_canonical_problem_tool_read_only(repo, settings):
    biz, tools = _make_tools(repo, settings)
    out = tools["match_canonical_problem_tool"]("patates soğuktu")
    assert "name" in out
    # read-only: does not create a canonical row
    assert repo.list_canonical_problems(biz) == []


def test_save_feedback_tool_and_idempotency(repo, settings):
    biz, tools = _make_tools(repo, settings)
    c = repo.upsert_customer(biz, "+905551112233")
    order = repo.create_order(biz, c["id"], "sent")
    call = repo.create_call(order["id"], c["id"], status="calling")
    fb = Feedback(satisfaction=2, sentiment="negative", missing_products=[
        {"name": "ayran", "urgency": "high", "notify": True}
    ])
    out = tools["save_feedback_tool"](call["id"], fb.model_dump_json())
    assert out["status"] == "saved"
    # idempotent: second call does not duplicate
    out2 = tools["save_feedback_tool"](call["id"], fb.model_dump_json())
    assert out2["status"] == "already_exists"
    assert len(repo.list_feedbacks(biz)) == 1


def test_update_dnc_list_tool(repo, settings):
    biz, tools = _make_tools(repo, settings)
    repo.upsert_customer(biz, "+905551112233")
    out = tools["update_dnc_list_tool"]("+905551112233")
    assert out == "updated"
    assert repo.get_customer(biz, "+905551112233")["do_not_call"] == 1


def test_record_agent_trace_tool(repo, settings):
    biz, tools = _make_tools(repo, settings)
    c = repo.upsert_customer(biz, "+905551112233")
    order = repo.create_order(biz, c["id"], "sent")
    call = repo.create_call(order["id"], c["id"], status="calling")
    tools["record_agent_trace_tool"](call["id"], "decide", "alarm")
    traces = repo.list_agent_traces(call["id"])
    assert len(traces) == 1
    assert traces[0]["step"] == "decide"


def test_propose_human_approval_tool_records_only(repo, settings):
    biz, tools = _make_tools(repo, settings)
    repo.upsert_customer(biz, "+905551112233")
    out = tools["propose_human_approval_tool"]("+905551112233", "loyal negative")
    assert out["status"] == "proposed"
    # never applies a discount: no order/call/refund created
    with _open(repo.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM actions").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 0


def test_send_telegram_alarm_tool(repo, settings):
    _, tools = _make_tools(repo, settings)
    out = _run(tools["send_telegram_alarm_tool"]("test alarm"))
    assert out == "sent"


def _open(db_path):
    from app.db import connect

    return connect(db_path)
