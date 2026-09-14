import asyncio

from app.analyzer import analyze_feedback
from app.pipeline import Pipeline


def _run(coro):
    return asyncio.run(coro)


def test_zero_data_nightly_synthesis_and_query(repo, settings):
    p = Pipeline(repo, settings)
    # Empty DB: synthesis must not crash and must be idempotent.
    insights = _run(p.nightly_synthesis())
    assert insights == []
    # Natural-language query on empty DB returns context without crashing.
    answer = _run(p.answer_query("en çok hangi sorun?"))
    assert isinstance(answer, str)


def test_empty_transcript_no_content(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    out = _run(p.process_call_result(call["id"], "   ", simulated=True))
    assert out is None
    assert repo.get_call(call["id"])["outcome"] == "no_content"


def test_turkish_chars_and_emoji_transcript(repo, settings):
    fb = analyze_feedback("Yemek çok güzeldi 😊 ama patatesler soğuk geldi 🥶", "tr")
    assert fb.sentiment in ("positive", "neutral", "negative")
    assert fb.summary  # summary captured


def test_very_long_transcript_truncated_summary():
    long_text = "çok güzel yemek " * 200
    fb = analyze_feedback(long_text, "tr")
    assert len(fb.summary) <= 400


def test_only_positive_and_only_negative():
    assert analyze_feedback("Harika, mükemmel, süper, teşekkürler", "tr").sentiment == "positive"
    assert analyze_feedback("Kötü, soğuk, bozuk, şikayet, pişman", "tr").sentiment == "negative"


def test_single_word_feedback(repo, settings):
    p = Pipeline(repo, settings)
    result = p.handle_order("+905551112233", status="sent")
    call = repo.create_call(result["order"]["id"], result["customer_id"], status="calling")
    out = _run(p.process_call_result(call["id"], "harika", simulated=True))
    assert out is not None
