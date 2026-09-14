import asyncio

from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def test_handle_order_created(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="created")
    assert result["status"] == "created"
    assert result["order"]["status"] == "created"


def test_handle_order_sent_schedules(repo, settings):
    p = Pipeline(repo, settings)
    p.handle_order("+905551112233", status="created")
    result = p.handle_order("+905551112233", status="sent")
    assert result["status"] == "sent"
    assert result["order"]["feedback_scheduled_at"] is not None


def test_do_not_call_blocked(repo, settings):
    p = Pipeline(repo, settings)
    p.handle_order("+905551112233", status="created")
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    repo.set_do_not_call(customer["id"])
    result = p.handle_order("+905551112233", status="created")
    assert result["status"] == "do_not_call"


def test_process_call_result_missing_product_alerts(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    order = result["order"]
    call = repo.create_call(order["id"], result["customer_id"], status="calling")
    out = _run(
        p.process_call_result(
            call["id"], "Ayran sipariş etmiştik ama gelmedi.", simulated=True
        )
    )
    assert out is not None
    assert out["decision"]["alert"] is True
    assert "ayran" in out["decision"]["alert_message"].lower()


def test_process_call_result_records_feedback(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    order = result["order"]
    call = repo.create_call(order["id"], result["customer_id"], status="calling")
    out = _run(
        p.process_call_result(call["id"], "Yemek çok güzeldi, teşekkürler.", simulated=True)
    )
    assert out is not None
    assert repo.get_call(call["id"])["status"] == "completed"
    assert len(repo.list_feedbacks(p.business_id)) == 1


def test_no_content_call(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    order = result["order"]
    call = repo.create_call(order["id"], result["customer_id"], status="calling")
    out = _run(p.process_call_result(call["id"], "   ", simulated=True))
    assert out is None
    assert repo.get_call(call["id"])["outcome"] == "no_content"


def test_answer_query_deterministic(repo, settings):
    p = Pipeline(repo, settings)
    p.handle_order("+905551112233", status="sent")
    answer = _run(p.answer_query("bu hafta en çok hangi sorun?"))
    assert isinstance(answer, str)
    assert "Recent problems" in answer


def test_nightly_synthesis(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    _run(p.process_call_result(call["id"], "Ayran gelmedi.", simulated=True))
    insights = _run(p.nightly_synthesis())
    assert len(insights) == 1
    assert insights[0]["insight_type"] == "daily_summary"


def test_normalize_phone():
    from app.pipeline import normalize_phone

    assert normalize_phone("05445974126") == "+905445974126"
    assert normalize_phone("+905445974126") == "+905445974126"
    assert normalize_phone(" 0544 597 41 26 ") == "+905445974126"


def test_trigger_immediate_call_no_calle(repo, settings):
    p = Pipeline(repo, settings)
    result = _run(p.trigger_immediate_call("05445974126"))
    assert result["phone"] == "+905445974126"
    assert result["status"] == "failed"  # calle_not_configured


def test_trigger_immediate_call_marks_notify(repo, settings):
    p = Pipeline(repo, settings)
    result = _run(p.trigger_immediate_call("05445974126"))
    call = repo.get_call(result["call_id"])
    assert call["notify_completion"] == 1


def test_simulate_call_full_pipeline(repo, settings):
    p = Pipeline(repo, settings)
    result = _run(
        p.simulate_call(
            "05445974126",
            "Yemek güzeldi ama ayran gelmedi.",
            notify_status=False,
        )
    )
    assert result["status"] == "completed"
    assert result["phone"] == "+905445974126"
    assert result["calle_call_id"].startswith("mock-")
    call = repo.get_call(result["call_id"])
    assert call["status"] == "completed"
    assert call["is_simulated"] == 1
    # feedback + insight persisted
    fb = repo.list_feedbacks(p.business_id)
    assert any(f["call_id"] == result["call_id"] for f in fb)
    assert result["processed"]["decision"]["alert"] is True


def test_operational_fact_beats_sentiment(repo, settings):
    """A polite/positive customer with a verified defect still escalates;
    an angry/negative customer with no concrete defect does not."""
    p = Pipeline(repo, settings)

    r1 = p.handle_order("+905551112233", status="sent")
    c1 = repo.create_call(r1["order"]["id"], r1["customer_id"], status="calling")
    out1 = _run(p.process_call_result(
        c1["id"], "Her şey çok güzeldi, teşekkürler. Sadece ayran gelmedi.", simulated=True
    ))
    assert out1["decision"]["alert"] is True  # verified defect > positive sentiment

    r2 = p.handle_order("+905559998877", status="sent")
    c2 = repo.create_call(r2["order"]["id"], r2["customer_id"], status="calling")
    out2 = _run(p.process_call_result(
        c2["id"], "Bu deneyimden hiç memnun kalmadım, çok kötüydü, bir daha almayacağım.", simulated=True
    ))
    assert out2["decision"]["alert"] is False  # negative sentiment alone does not alert
