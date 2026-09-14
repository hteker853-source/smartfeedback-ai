"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import db, notify
from .calle import classify_outcome, extract_customer_text
from .config import get_settings
from .health import actionable_notices, dependency_status, overall_status
from .logging_setup import setup_logging
from .pipeline import Pipeline
from .repository import Repository
from .scheduler import Scheduler
from .telegram_bot import TelegramBot
from .trends import classify_problem

logger = logging.getLogger(__name__)

settings = get_settings()
repo: Repository | None = None
pipeline: Pipeline | None = None
scheduler: Scheduler | None = None
telegram: TelegramBot | None = None

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global repo, pipeline, scheduler, telegram
    setup_logging(settings)
    db.init_db(settings.database_file)
    repo = Repository(settings.database_file)
    pipeline = Pipeline(repo, settings)
    scheduler = Scheduler(pipeline, repo)
    telegram = TelegramBot(settings, pipeline, repo)

    tasks = [
        asyncio.create_task(scheduler.run(), name="scheduler"),
    ]
    try:
        await telegram.start()
    except Exception:  # noqa: BLE001
        logger.exception("Telegram failed to start; continuing without it")

    # Notify the admin once at startup if a critical dependency is missing.
    try:
        deps = dependency_status(settings, settings.database_file)
        for notice in actionable_notices(deps):
            await notify.send_admin(notice)
    except Exception:  # noqa: BLE001
        logger.exception("startup dependency notice failed")

    yield

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await telegram.stop()


app = FastAPI(title="SmartFeedback AI", lifespan=lifespan)


def _require_api_token(x_api_token: str | None = Header(default=None)) -> None:
    """Guard mutating endpoints when an API token is configured."""
    if settings.api_token and x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
async def health() -> dict[str, Any]:
    deps = dependency_status(settings, settings.database_file)
    return {"status": overall_status(deps), "dependencies": deps}


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    index = _WEB_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>SmartFeedback AI</h1><p>Dashboard not found.</p>")


@app.get("/api/summary")
async def summary() -> dict[str, Any]:
    assert pipeline is not None and repo is not None
    business_id = pipeline.business_id

    now = datetime.now(timezone.utc)
    recent_since = (now - timedelta(days=7)).isoformat(timespec="seconds")
    prev_since = (now - timedelta(days=14)).isoformat(timespec="seconds")

    problems = repo.list_canonical_problems(business_id)
    problem_trends: list[dict[str, Any]] = []
    for p in problems:
        total = p["count"] or 0
        recent = repo.count_problem_mentions_since(business_id, p["id"], recent_since)
        prev_total = repo.count_problem_mentions_since(business_id, p["id"], prev_since)
        prev = prev_total - recent
        status, direction = classify_problem(
            total, recent, prev, pipeline._days_ago(p.get("last_seen_at"))
        )
        label = {
            "increasing": "rising", "new": "rising",
            "decreasing": "improving", "stable": "stable", "unknown": "stable",
        }.get(direction, "stable")
        problem_trends.append(
            {"name": p["name"], "count": total, "direction": label, "status": status}
        )

    feedbacks = repo.list_feedbacks(business_id, limit=1)
    last = feedbacks[0] if feedbacks else None
    last_feedback: dict[str, Any] | None = None
    if last is not None:
        missing = _json_list(last["missing_products"])
        priority = last["priority"] or ""
        sentiment = last["sentiment"] or ""
        decision = (
            "alarm" if missing
            else "özet" if (priority in ("critical", "high", "medium") or sentiment == "negative")
            else "dokunma"
        )
        last_feedback = {
            "id": last["id"],
            "call_id": last["call_id"],
            "transcript": last["transcript"],
            "satisfaction": last["satisfaction"],
            "sentiment": sentiment,
            "priority": priority,
            "missing": [m["name"] for m in missing] if missing else [],
            "decision": decision,
        }

    n_recurring = sum(1 for p in problems if (p["count"] or 0) >= 3)

    # Decision Card: surface the full evidence -> verification -> policy chain of
    # the last analyzed call, from the persisted decision envelope + its case.
    decision_card: dict[str, Any] | None = None
    if last is not None:
        call = repo.get_call(last["call_id"])
        env: dict[str, Any] | None = None
        if call and call.get("decision_json"):
            try:
                env = json.loads(call["decision_json"])
            except (TypeError, ValueError):
                env = None
        if env:
            case = repo.get_case(env.get("case_id")) if env.get("case_id") else None
            evidence_span = None
            for c in env.get("claims", []):
                for s in c.get("transcript_spans", []):
                    if s.get("text"):
                        evidence_span = s["text"]
                        break
                if evidence_span:
                    break
            decision_card = {
                "customer_statement": last["transcript"],
                "evidence_span": evidence_span,
                "verification": env.get("verification", []),
                "confidence": env.get("confidence"),
                "impact": env.get("impact"),
                "trend_direction": env.get("trend_direction"),
                "action": env.get("action"),
                "decision": env.get("decision"),
                "dissent": env.get("dissent"),
                "case": case,
                "provenance": env.get("provenance"),
            }

    # Prefer the real agent trace recorded by StrandsAnalyzer._record_findings
    # (feedback/investigate/trend/decide); fall back to a computed 3-step
    # trace when no trace exists yet (e.g. the deterministic analyzer ran).
    traces: list[dict[str, Any]] = []
    if last_feedback is not None:
        traces = repo.list_agent_traces(last_feedback["call_id"])
    if traces:
        tool_trace = [
            {
                "step": t["step"],
                "label": t["step"],
                "out": t["detail"] or "",
                # GÖREV D: the agent's own one-sentence account of what it
                # found/decided, straight from its LLM output — reporting
                # only, never fed back into any decision.
                "reasoning": t.get("reasoning_summary") or "",
            }
            for t in traces
        ]
    else:
        tool_trace = [
            {"step": "feedback", "label": "Feedback — transkript analizi",
             "out": f"{last_feedback['sentiment']} · {last_feedback['satisfaction']}/5 · {last_feedback['priority']}" if last_feedback else "—",
             "reasoning": ""},
            {"step": "investigate", "label": "Investigate — geçmiş sorgusu",
             "out": f"{n_recurring} tekrarlayan sorun", "reasoning": ""},
            {"step": "decide", "label": "Decide — karar",
             "out": last_feedback["decision"] if last_feedback else "—", "reasoning": ""},
        ]

    return {
        "business_name": pipeline.business_name,
        "problems": problems,
        "insights": repo.list_insights(business_id, limit=50),
        "feedbacks": repo.list_feedbacks(business_id, limit=100),
        "last_feedback": last_feedback,
        "problem_trends": problem_trends,
        "tool_trace": tool_trace,
        "decision_card": decision_card,
        "cases": repo.list_cases(business_id, limit=50),
        "foreign_customer_trend": pipeline.compute_foreign_customer_trend(),
    }


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


@app.get("/api/problems")
async def problems() -> list[dict[str, Any]]:
    assert pipeline is not None
    return repo.list_canonical_problems(pipeline.business_id)


@app.get("/api/insights")
async def insights() -> list[dict[str, Any]]:
    assert pipeline is not None
    return repo.list_insights(pipeline.business_id, limit=100)


@app.get("/api/feedbacks")
async def feedbacks() -> list[dict[str, Any]]:
    assert pipeline is not None
    return repo.list_feedbacks(pipeline.business_id, limit=200)


@app.get("/api/cases")
async def cases() -> list[dict[str, Any]]:
    assert pipeline is not None
    return repo.list_cases(pipeline.business_id, limit=100)


@app.get("/api/cases/{case_id}/trace")
async def case_trace(case_id: int) -> dict[str, Any]:
    assert repo is not None
    trace = repo.get_case_trace(case_id)
    if trace is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return trace


@app.post("/api/orders", dependencies=[Depends(_require_api_token)])
async def create_order(payload: dict[str, Any]) -> dict[str, Any]:
    assert pipeline is not None
    phone = str(payload.get("phone", "")).strip()
    if not phone:
        return JSONResponse({"error": "phone required"}, status_code=400)
    status = payload.get("status", "created")
    locale = payload.get("locale", "tr")
    order_type = payload.get("order_type", "service")
    result = pipeline.handle_order(phone, status, order_type, locale)
    if result.get("status") == "invalid_phone":
        return JSONResponse({"error": result.get("error")}, status_code=400)
    return result


@app.post("/api/test-call", dependencies=[Depends(_require_api_token)])
async def test_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Trigger an immediate CALL-E call (no scheduler delay). Completion is
    picked up by the scheduler's poll loop and reported via Telegram."""
    assert pipeline is not None
    phone = str(payload.get("phone", "")).strip()
    if not phone:
        return JSONResponse({"error": "phone required"}, status_code=400)
    locale = payload.get("locale", "tr")
    result = await pipeline.trigger_immediate_call(phone, locale)
    if result.get("status") == "invalid_phone":
        return JSONResponse({"error": result.get("error")}, status_code=400)
    return result


@app.get("/api/calls/{call_id}")
async def get_call_status(call_id: int) -> dict[str, Any]:
    assert repo is not None
    call = repo.get_call(call_id)
    if call is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    order = repo.get_order(call["order_id"])
    return {
        "call": call,
        "order": order,
    }


@app.post("/api/mock-call", dependencies=[Depends(_require_api_token)])
async def mock_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Simulate a completed CALL-E call with a given transcript and run the
    full pipeline. No real call is placed."""
    assert pipeline is not None
    phone = str(payload.get("phone", "05445974126")).strip()
    transcript = str(
        payload.get(
            "transcript",
            "Yemek çok güzeldi ama patatesler soğuk geldi. Ayran da gelmedi.",
        )
    )
    locale = payload.get("locale", "tr")
    result = await pipeline.simulate_call(phone, transcript, locale, notify_status=True)
    if result.get("status") == "invalid_phone":
        return JSONResponse({"error": result.get("error")}, status_code=400)
    return result


@app.post("/api/webhooks/calle")
async def calle_webhook(request: Request) -> dict[str, Any]:
    assert pipeline is not None
    event_id = request.headers.get("CALL-E-Event-Id") or request.headers.get(
        "X-Calle-Event-Id"
    )
    body = await request.json()
    if not event_id:
        event_id = body.get("event_id") or body.get("id")

    calle_call_id = body.get("call_id") or (body.get("call") or {}).get("id")

    # At-least-once delivery: deduplicate on the event id before side effects.
    if event_id:
        if not repo.record_webhook_event(str(event_id), str(calle_call_id) if calle_call_id else None):
            return {"status": "duplicate"}

    if not calle_call_id:
        return {"status": "ignored"}
    call = repo.get_call_by_calle_id(str(calle_call_id))
    if call is None:
        return {"status": "unknown_call"}
    event = body.get("type", "")
    if event == "call.failed":
        repo.update_call(call["id"], status="failed", failure_code="call_failed")
        return {"status": "failed"}
    call_obj = body.get("call")
    if not call_obj and pipeline.calle.enabled:
        try:
            call_obj = pipeline.calle.fetch_call(str(calle_call_id))
        except Exception:  # noqa: BLE001
            call_obj = None
    if call_obj is None:
        return {"status": "no_data"}
    outcome = classify_outcome(call_obj)
    if outcome in ("no_answer", "voicemail"):
        repo.update_call(call["id"], status="no_answer", outcome=outcome)
        return {"status": outcome}
    transcript = extract_customer_text(call_obj)
    await pipeline.process_call_result(call["id"], transcript)
    return {"status": "processed"}


@app.post("/api/demo", dependencies=[Depends(_require_api_token)])
async def demo(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    assert pipeline is not None
    payload = payload or {}
    transcript = payload.get(
        "transcript",
        "Yemek çok güzeldi ama patatesler soğuk geldi. Ayran da gelmedi.",
    )
    phone = payload.get("phone", "+905551112233")
    result = pipeline.handle_order(phone, status="sent")
    if result.get("status") == "invalid_phone":
        return JSONResponse({"error": result.get("error")}, status_code=400)
    if result.get("status") == "do_not_call":
        return {"status": "do_not_call"}
    order = result["order"]
    from datetime import datetime, timezone

    call = repo.create_call(
        order["id"], result["customer_id"], status="calling",
        scheduled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    processed = await pipeline.process_call_result(
        call["id"], transcript, simulated=True
    )
    if processed is None:
        return {"status": "no_content"}
    return processed


@app.post("/mock/downstream-action")
async def mock_downstream_action(payload: dict[str, Any]) -> dict[str, Any]:
    """Self-hosted sandbox receiver for `app/actions.py::execute_approved_action`.

    Stands in for an external downstream system (CRM, refund processor,
    etc.) so `SANDBOX_ACTION_WEBHOOK_URL` can point somewhere real without
    depending on a third-party service. Logs the received action and
    returns a fake-but-realistic acknowledgement; performs no real side
    effect of its own.
    """
    import uuid

    logger.info(
        "mock downstream action received: action_id=%s action_type=%s decision=%s",
        payload.get("action_id"), payload.get("action_type"), payload.get("decision"),
    )
    return {
        "status": "processed",
        "reference_id": f"MOCK-{uuid.uuid4().hex[:8]}",
    }
