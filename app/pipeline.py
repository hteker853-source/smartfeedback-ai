"""End-to-end pipeline.

Order -> schedule -> CALL-E -> transcript -> Strands analysis -> DB -> notify.

Deterministic aggregation (counts, trends, loyalty, churn, recurring-problem
tracking) lives in Python/SQL. The LLM (Strands/Bedrock) is used for
understanding and explanation, with a deterministic fallback.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from . import evidence, impact as impact_mod, notify, policy
from .analyzer import analyze_feedback as _deterministic_analyze, detect_dnc
from .calle import CalleService, classify_outcome, extract_customer_text
from .canonical import CanonicalMatcher
from .config import Settings
from .db import _json_dump, connect as _connect
from .llm import complete_json, get_llm
from .phones import (
    calle_error_message,
    normalize_phone,
    region_from_phone,
    validate_phone,
)
from .repository import Repository
from .schemas import DecisionEnvelope, Feedback, ImpactScore
from .trends import (
    classify_problem,
    classify_problem_rich,
    classify_satisfaction_trend,
    compute_customer_rhythm,
    percent_change,
)

try:
    from calle import CalleAPIError
except ImportError:  # pragma: no cover
    CalleAPIError = Exception


@contextmanager
def _conn(db_path: Any) -> Iterator[Any]:
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# A customer is a re-engagement candidate once this many days have passed
# since their last order. Documented, not magic: matches the task's explicit
# "15 günden fazla" requirement.
DORMANT_DAYS = 15


def _extract_percentage(decision: str | None) -> int | None:
    """Parse "approved:50%" -> 50. Returns None if no number is present."""
    if not decision:
        return None
    m = re.search(r"(\d+)", decision)
    return int(m.group(1)) if m else None


class Pipeline:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings
        self.calle = CalleService(settings)
        self.matcher = CanonicalMatcher(repo, settings)
        self._business = repo.get_or_create_business(
            settings.business_name, settings.business_default_locale
        )
        self.business_id = self._business["id"]
        self._llm = get_llm(settings)
        self._analyzer: Any | None = None

    @property
    def business_name(self) -> str:
        return self._business["name"]

    def refresh(self) -> None:
        """Rebuild components that depend on runtime-updated secrets (AWS/CALL-E)."""
        self._llm = get_llm(self.settings)
        self._analyzer = None
        self.calle = CalleService(self.settings)
        self.matcher = CanonicalMatcher(self.repo, self.settings)

    def _get_analyzer(self) -> Any:
        if self.settings.llm_enabled and self._analyzer is None:
            from .agents import StrandsAnalyzer

            self._analyzer = StrandsAnalyzer(self.repo, self.settings, self.business_id)
        return self._analyzer

    # -- order intake ----------------------------------------------------
    def handle_order(
        self,
        phone: str,
        status: str = "created",
        order_type: str = "service",
        locale: str = "tr",
    ) -> dict[str, Any]:
        ok, normalized, err = validate_phone(phone, self.settings.supported_regions)
        if not ok:
            return {"status": "invalid_phone", "error": err, "phone": phone}
        customer = self.repo.upsert_customer(self.business_id, normalized)
        if customer["do_not_call"]:
            return {"status": "do_not_call", "customer_id": customer["id"]}

        if status == "sent":
            open_order = self.repo.get_open_order_for_customer(customer["id"])
            if open_order is None:
                open_order = self.repo.create_order(
                    self.business_id, customer["id"], "created", order_type, locale
                )
            order = self.repo.mark_order_sent(
                open_order["id"], self.settings.post_delivery_delay_minutes
            )
        else:
            order = self.repo.create_order(
                self.business_id, customer["id"], "created", order_type, locale
            )

        self.repo.update_customer_stats(customer["id"])
        return {"status": order["status"], "order": order, "customer_id": customer["id"]}

    # -- call scheduling -------------------------------------------------
    async def run_due_calls(self) -> int:
        due = self.repo.list_orders_due_for_feedback()
        count = 0
        for order in due:
            await self._dispatch_call(order)
            count += 1
        return count

    async def _dispatch_call(
        self, order: dict[str, Any], *, notify_completion: bool = False
    ) -> dict[str, Any] | None:
        customer = self._load_customer(order["customer_id"])
        if customer is None:
            return None
        if customer["do_not_call"]:
            return self.repo.create_call(
                order["id"], customer["id"], status="do_not_call", scheduled_at=_now()
            )

        try:
            call = self.repo.create_call(
                order["id"], customer["id"], status="calling", scheduled_at=_now(),
                notify_completion=1 if notify_completion else 0,
            )
        except sqlite3.IntegrityError:
            # Another worker already created a call for this order.
            return None
        if not self.calle.enabled:
            self.repo.update_call(
                call["id"], status="failed",
                failure_code="calle_not_configured", completed_at=_now(),
            )
            return self.repo.get_call(call["id"])
        try:
            result = self.calle.place_call(
                phone=customer["phone"],
                business_name=self.business_name,
                locale=order.get("locale", "tr"),
                order_id=order["id"],
                correlation_id=order.get("correlation_id", ""),
                business_id=self.business_id,
                environment=self.settings.environment,
                context=self._build_call_context(customer),
                preferred_language=customer.get("preferred_language"),
            )
            self.repo.update_call(
                call["id"], calle_call_id=str(result.get("id")), started_at=_now()
            )
            return self.repo.get_call(call["id"])
        except CalleAPIError as exc:
            code = getattr(exc, "code", "") or ""
            message = calle_error_message(code) or getattr(exc, "message", "")
            self.repo.update_call(
                call["id"], status="failed",
                failure_code=f"{code}: {message}".strip(": "),
            )
            return self.repo.get_call(call["id"])
        except Exception as exc:  # noqa: BLE001
            self.repo.update_call(
                call["id"], status="failed", failure_code=str(exc)[:200]
            )
            return self.repo.get_call(call["id"])

    def _build_call_context(self, customer: dict[str, Any]) -> str | None:
        """Build a safe pre-call contextualization from the customer's verified,
        structured case history. Returns None when there is nothing reliable.

        Safety: only canonical problem *names* are used (never raw transcripts,
        never PII); only cases whose confidence meets the threshold qualify, and
        DNC is handled before this is ever called.

        Returns a language-neutral, comma-separated list of problem names (not
        a pre-phrased sentence): the call-script LLM (see `CalleService`) is
        responsible for wording this naturally in the customer's language, so
        no per-language template is hardcoded here.
        """
        problems = self.repo.customer_verified_problems(
            self.business_id,
            customer["id"],
            min_confidence=self.settings.context_min_confidence,
            limit=self.settings.context_max_items,
        )
        if not problems:
            return None
        return ", ".join(p["name"] for p in problems)

    # -- immediate test call ---------------------------------------------
    async def trigger_immediate_call(
        self, phone: str, locale: str = "tr"
    ) -> dict[str, Any]:
        """Create/update the customer and dispatch a CALL-E call right now,
        bypassing the scheduler. Notifies Telegram on start."""
        ok, phone, err = validate_phone(phone, self.settings.supported_regions)
        if not ok:
            return {"status": "invalid_phone", "error": err}
        customer = self.repo.upsert_customer(self.business_id, phone)
        if customer["do_not_call"]:
            return {"status": "do_not_call", "phone": phone}

        order = self.repo.create_order(
            self.business_id, customer["id"], "sent", locale=locale
        )
        self.repo.mark_order_sent(order["id"], 0)
        order = self.repo.get_order(order["id"])
        self.repo.update_customer_stats(customer["id"])

        call = await self._dispatch_call(order, notify_completion=True)
        if call is None:
            return {"status": "error", "phone": phone}

        status = call.get("status")
        if status == "failed":
            await notify.send_admin(
                f"❌ Arama başlatılamadı\nNumara: {phone}\n"
                f"Neden: {call.get('failure_code') or 'bilinmiyor'}"
            )
        else:
            await notify.send_admin(
                f"🔔 Test araması başlatıldı\n"
                f"Numara: {phone}\n"
                f"CALL-E call: {call.get('calle_call_id') or '—'}\n"
                f"Durum: {status}"
            )
        return {
            "status": status,
            "phone": phone,
            "call_id": call["id"],
            "order_id": order["id"],
            "calle_call_id": call.get("calle_call_id"),
        }

    async def poll_and_finalize(
        self, call_id: int, *, notify_status: bool = True
    ) -> dict[str, Any]:
        """Fetch a CALL-E call's result and process it once it reaches a
        terminal state. Returns the current status."""
        call = self.repo.get_call(call_id)
        if call is None:
            return {"status": "missing"}
        if call["status"] == "completed":
            return {"status": "completed"}

        calle_call_id = call.get("calle_call_id")
        if not calle_call_id or not self.calle.enabled:
            return {"status": call["status"]}

        result = self.calle.fetch_call(calle_call_id)
        outcome = classify_outcome(result)
        if outcome in ("failed", "canceled"):
            self.repo.update_call(call_id, status="failed", failure_code=outcome)
            if notify_status:
                await notify.send_admin(f"❌ Arama başarısız oldu ({outcome}).")
            return {"status": "failed"}

        if outcome in ("no_answer", "voicemail"):
            if (call.get("retry_count") or 0) == 0:
                retry_at = (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(timespec="seconds")
                self.repo.update_call(
                    call_id, status="retry_scheduled", outcome=outcome,
                    retry_count=1, retry_at=retry_at,
                )
                if notify_status:
                    await notify.send_admin(
                        f"ℹ️ Arama cevapsız kaldı ({outcome}). "
                        "5 dakika sonra tekrar denenecek."
                    )
                return {"status": "retry_scheduled"}
            self.repo.update_call(
                call_id, status="no_answer", outcome=outcome, completed_at=_now()
            )
            if notify_status:
                await notify.send_admin(f"ℹ️ Arama cevapsız kaldı ({outcome}).")
            return {"status": outcome}

        if outcome == "completed":
            transcript = extract_customer_text(result)
            processed = await self.process_call_result(
                call_id, transcript, notify_status=notify_status
            )
            if processed is None:
                self.repo.update_call(call_id, status="completed", outcome="no_content")
                if notify_status:
                    await notify.send_admin("ℹ️ Arama tamamlandı (transcript yok).")
            return {"status": "completed"}

        return {"status": outcome or "in_progress"}

    async def redial_due_calls(self) -> int:
        """Re-place ONE retry CALL-E attempt for calls whose first attempt
        was no_answer/voicemail and whose 5-minute retry window has arrived.

        Reuses the SAME `calls` row (no second row is created), so the
        one-call-per-order constraint is untouched. At most one retry per
        call: `retry_count` is already 1 by the time a call reaches here
        (set when the retry was scheduled), so a second no_answer/voicemail
        after this redial finalizes the call as terminal instead of looping.
        """
        with _connect(self.repo.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM calls WHERE status = 'retry_scheduled'
                   AND retry_at IS NOT NULL AND retry_at <= ?""",
                (_now(),),
            ).fetchall()
            due = [dict(r) for r in rows]

        redialed = 0
        for call in due:
            order = self.repo.get_order(call["order_id"])
            customer = self._load_customer(call["customer_id"])
            if order is None or customer is None or customer["do_not_call"]:
                self.repo.update_call(
                    call["id"], status="failed", failure_code="retry_skipped",
                    completed_at=_now(),
                )
                continue
            if not self.calle.enabled:
                self.repo.update_call(
                    call["id"], status="failed", failure_code="calle_not_configured",
                    completed_at=_now(),
                )
                continue
            try:
                result = self.calle.place_call(
                    phone=customer["phone"],
                    business_name=self.business_name,
                    locale=order.get("locale", "tr"),
                    order_id=order["id"],
                    correlation_id=order.get("correlation_id", ""),
                    business_id=self.business_id,
                    environment=self.settings.environment,
                    context=self._build_call_context(customer),
                    preferred_language=customer.get("preferred_language"),
                )
                self.repo.update_call(
                    call["id"], status="calling", calle_call_id=str(result.get("id")),
                    started_at=_now(), retry_at=None,
                )
                redialed += 1
            except Exception as exc:  # noqa: BLE001
                self.repo.update_call(
                    call["id"], status="failed", failure_code=str(exc)[:200],
                    completed_at=_now(),
                )
        return redialed

    # -- mock / simulated call -------------------------------------------
    async def simulate_call(
        self,
        phone: str,
        transcript: str,
        locale: str = "tr",
        *,
        notify_status: bool = True,
    ) -> dict[str, Any]:
        """Run the full pipeline as if CALL-E completed a successful call with
        the given transcript. No real call is placed; the call is marked
        simulated. Used to test DB -> analysis -> Telegram end to end."""
        ok, phone, err = validate_phone(phone, set())
        if not ok:
            return {"status": "invalid_phone", "error": err}
        customer = self.repo.upsert_customer(self.business_id, phone)
        if customer["do_not_call"]:
            return {"status": "do_not_call", "phone": phone}

        order = self.repo.create_order(
            self.business_id, customer["id"], "sent", locale=locale
        )
        self.repo.mark_order_sent(order["id"], 0)
        order = self.repo.get_order(order["id"])

        call = self.repo.create_call(
            order["id"], customer["id"], status="calling",
            scheduled_at=_now(), notify_completion=1,
        )
        mock_calle_id = f"mock-{call['id']}"
        self.repo.update_call(
            call["id"], calle_call_id=mock_calle_id, started_at=_now()
        )

        if notify_status:
            await notify.send_admin(
                f"🔔 [MOCK] Test araması başlatıldı\n"
                f"Numara: {phone}\nCALL-E call: {mock_calle_id}\n"
                f"Durum: calling (simüle)"
            )

        processed = await self.process_call_result(
            call["id"], transcript, simulated=True, notify_status=notify_status
        )
        return {
            "status": "completed",
            "phone": phone,
            "call_id": call["id"],
            "order_id": order["id"],
            "calle_call_id": mock_calle_id,
            "processed": processed,
        }

    # -- result processing ----------------------------------------------
    async def process_call_result(
        self, call_id: int, transcript: str, *,
        simulated: bool = False, notify_status: bool = False,
    ) -> dict[str, Any] | None:
        call = self.repo.get_call(call_id)
        if call is None:
            return None
        if self.repo.feedback_exists(call_id):
            return None
        order = self.repo.get_order(call["order_id"])
        customer = self._load_customer(call["customer_id"])

        if not transcript.strip():
            self.repo.update_call(
                call_id, status="completed", outcome="no_content", completed_at=_now()
            )
            if notify_status:
                await notify.send_admin(
                    f"ℹ️ Arama tamamlandı (içerik yok).\nNumara: {customer['phone'] if customer else '—'}"
                )
            return None

        locale = (order or {}).get("locale", "tr")
        phone = customer["phone"] if customer else ""

        # "Beni bir daha aramayın" — treat the transcript as untrusted content,
        # detect only the narrow explicit request, and set the DNC flag.
        dnc_requested = detect_dnc(transcript, locale)
        if dnc_requested and customer is not None:
            self.repo.set_do_not_call(customer["id"])
            self.repo.record_agent_trace(call_id, "dnc", "do_not_call set from transcript")

        # Persist the transcript before running the agent, so the supervisor's
        # tools (e.g. save_feedback_tool) read it from the DB.
        self.repo.update_call(call_id, transcript=transcript)

        feedback, envelope, saved = await self._analyze_and_decide(
            call, transcript, locale, phone
        )
        if envelope is None:
            return None  # concurrent worker already processed this call
        if saved is None:
            # Safety net: the agentic supervisor did not persist; save here.
            try:
                saved = self.repo.save_feedback(
                    call_id=call_id,
                    order_id=call["order_id"],
                    customer_id=call["customer_id"],
                    business_id=self.business_id,
                    feedback=feedback,
                    transcript=transcript,
                )
            except sqlite3.IntegrityError:
                return None
            self._canonicalize(saved, feedback)

        envelope.dnc_requested = dnc_requested
        envelope.case_id = self._upsert_cases(envelope)

        now = _now()
        self.repo.update_call(
            call_id,
            status="completed",
            outcome="transcribed",
            transcript=transcript,
            result_json=feedback.model_dump_json(),
            decision_json=envelope.model_dump_json(),
            is_simulated=1 if simulated else 0,
            completed_at=_now(),
        )
        if order is not None:
            with _conn(self.repo.db_path) as conn:
                conn.execute(
                    "UPDATE orders SET status = 'completed', completed_at = ? WHERE id = ?",
                    (now, order["id"]),
                )
        self.repo.update_customer_stats(call["customer_id"])

        await self._persist_and_notify(envelope, feedback, customer, call_id=call_id)

        if notify_status:
            missing = [m.name for m in feedback.missing_products]
            await notify.send_admin(
                "✅ Arama tamamlandı\n"
                f"Numara: {customer['phone'] if customer else '—'}\n"
                f"Memnuniyet: {feedback.satisfaction if feedback.satisfaction is not None else '—'}/5 | "
                f"Duygu: {feedback.sentiment}\n"
                f"Öncelik: {feedback.priority}\n"
                f"Eksik ürün: {', '.join(missing) if missing else 'yok'}"
            )
        return {
            "feedback": saved,
            "decision": envelope.decision.model_dump(),
            "action": envelope.action,
            "confidence": envelope.confidence.model_dump(),
            "verification": [v.model_dump() for v in envelope.verification],
            "dissent": [d.model_dump() for d in envelope.dissent],
            "case_id": envelope.case_id,
            "impact": envelope.impact.model_dump() if envelope.impact else None,
            "trend_direction": envelope.trend_direction,
            "trend_confidence": envelope.trend_confidence,
        }

    async def _analyze_and_decide(
        self,
        call: dict[str, Any],
        transcript: str,
        locale: str,
        phone: str,
    ) -> tuple[Feedback, DecisionEnvelope | None, dict[str, Any] | None]:
        """Return (feedback, envelope, saved). Agentic supervisor path when the LLM
        is available; deterministic path otherwise. The LLM only *proposes*; the
        deterministic policy gate produces the authoritative decision."""
        analyzer = self._get_analyzer()
        if analyzer is not None:
            try:
                feedback = await analyzer.analyze_feedback(transcript, locale)
                await self._maybe_set_preferred_language(
                    analyzer, transcript, call["customer_id"]
                )
                decision = await analyzer.run(transcript, phone, call["id"], feedback)
                saved = self.repo.get_feedback_for_call(call["id"])
                envelope = self._decide(
                    feedback, transcript, locale, proposed_alert=bool(decision.alert)
                )
                envelope.provenance = analyzer.last_provenance
                return feedback, envelope, saved
            except Exception:  # noqa: BLE001
                pass

        return self._deterministic_analyze_and_decide(call, transcript, locale)

    async def _maybe_set_preferred_language(
        self, analyzer: Any, transcript: str, customer_id: int
    ) -> None:
        """Detect and persist the customer's spoken language, once.

        Best-effort and fully isolated: any failure here must never affect the
        main feedback/decision path, and once set the value is never
        overwritten (persisted once, `Repository.set_preferred_language` is
        itself guarded at the SQL level against a concurrent overwrite).
        """
        try:
            customer = self._load_customer(customer_id)
            if customer is None or customer.get("preferred_language"):
                return
            code = await analyzer.detect_language(transcript)
            if code and code != "und":
                self.repo.set_preferred_language(customer_id, code)
        except Exception:  # noqa: BLE001
            pass

    def _deterministic_analyze_and_decide(
        self, call: dict[str, Any], transcript: str, locale: str
    ) -> tuple[Feedback, DecisionEnvelope | None, dict[str, Any] | None]:
        feedback = _deterministic_analyze(transcript, locale)
        try:
            saved = self.repo.save_feedback(
                call_id=call["id"],
                order_id=call["order_id"],
                customer_id=call["customer_id"],
                business_id=self.business_id,
                feedback=feedback,
                transcript=transcript,
            )
        except sqlite3.IntegrityError:
            return feedback, None, None
        self._canonicalize(saved, feedback)
        envelope = self._decide(
            feedback, transcript, locale, proposed_alert=bool(feedback.missing_products)
        )
        return feedback, envelope, saved

    def _decide(
        self, feedback: Feedback, transcript: str, locale: str, *, proposed_alert: bool = False
    ) -> DecisionEnvelope:
        """Run the evidence -> verification -> confidence -> policy-gate chain."""
        claims = evidence.extract_claims(feedback, transcript, locale)
        verifications = evidence.verify_claims(claims, transcript, locale)
        refs = self._canonical_refs(claims)
        occurrence = {c.id: (refs[c.id][1] if c.id in refs else 0) for c in claims}
        max_occurrence = max(occurrence.values(), default=0)
        confidence = evidence.aggregate_confidence(claims, verifications, max_occurrence)

        impact, trend_direction, trend_confidence = self._compute_case_impact(
            claims, verifications, refs
        )

        decision, action, dissent = policy.evaluate(
            feedback,
            claims,
            verifications,
            confidence,
            occurrence,
            min_alert_confidence=self.settings.min_alert_confidence,
            recurring_threshold=self.settings.recurring_threshold,
            proposed_alert=proposed_alert,
            impact=impact,
        )
        return DecisionEnvelope(
            decision=decision,
            action=action,
            confidence=confidence,
            verification=verifications,
            claims=claims,
            dissent=dissent,
            impact=impact,
            trend_direction=trend_direction,
            trend_confidence=trend_confidence,
        )

    def _compute_case_impact(
        self,
        claims: list[Any],
        verifications: list[Any],
        refs: dict[str, tuple[int | None, int]],
    ) -> tuple[ImpactScore | None, str, float | None]:
        """Compute the business-impact of the primary (highest-occurrence)
        verified claim, using deterministic recurrence + trend + severity."""
        verified = {v.claim_id for v in verifications if v.status == "verified"}
        candidates: list[tuple[Any, int, int]] = []
        for c in claims:
            if c.id not in verified:
                continue
            ref = refs.get(c.id)
            if ref is None or ref[0] is None:
                continue
            candidates.append((c, ref[0], ref[1]))
        if not candidates:
            return None, "unknown", None

        candidates.sort(key=lambda x: -x[2])
        primary, canon_id, count = candidates[0]
        canon = self.repo.get_canonical_problem_by_id(canon_id)
        last_seen = self._days_ago(canon["last_seen_at"]) if canon else None

        now = datetime.now(timezone.utc)
        recent = self.repo.count_problem_mentions_since(
            self.business_id, canon_id, (now - timedelta(days=7)).isoformat(timespec="seconds")
        )
        prev_total = self.repo.count_problem_mentions_since(
            self.business_id, canon_id, (now - timedelta(days=14)).isoformat(timespec="seconds")
        )
        prev = prev_total - recent
        trend = classify_problem_rich(count, recent, prev, last_seen)
        affected = self.repo.count_affected_customers(canon_id)
        impact = impact_mod.compute_impact(
            occurrence_count=count,
            severity=primary.severity,
            affected_customers=affected,
            trend_direction=trend.direction,
            last_seen_days_ago=last_seen,
        )
        return impact, trend.direction, trend.trend_confidence

    def _canonical_refs(self, claims: list[Any]) -> dict[str, tuple[int | None, int]]:
        """Read-only map claim_id -> (canonical_problem_id, occurrence_count)."""
        refs: dict[str, tuple[int | None, int]] = {}
        for c in claims:
            m = self.matcher.match(self.business_id, c.text)
            refs[c.id] = (m.get("id"), m.get("count", 0))
        return refs

    def _upsert_cases(self, envelope: DecisionEnvelope) -> int | None:
        """Fold verified claims into persistent cases (cross-call recurrence)."""
        verified = {v.claim_id for v in envelope.verification if v.status == "verified"}
        refs = self._canonical_refs(envelope.claims)
        dissent_json = _json_dump([d.model_dump() for d in envelope.dissent])
        case_ids: list[int] = []
        for c in envelope.claims:
            if c.id not in verified:
                continue
            ref = refs.get(c.id)
            if ref is None or ref[0] is None:
                continue
            canon_id, _count = ref
            case = self.repo.upsert_case(
                self.business_id,
                canon_id,
                c.text,
                severity=c.severity,
                confidence=envelope.confidence.total,
                evidence_json=_json_dump([s.model_dump() for s in c.transcript_spans]),
                decision=envelope.action,
                dissent_json=dissent_json,
                last_decision_action=envelope.action,
                recommended_action=envelope.decision.recommended_action,
                affected_customer_count=self.repo.count_affected_customers(canon_id),
                impact_score=envelope.impact.score if envelope.impact else 0.0,
                trend_direction=envelope.trend_direction,
            )
            case_ids.append(case["id"])
        return case_ids[0] if case_ids else None

    def _canonicalize(self, saved: dict[str, Any], feedback: Feedback) -> None:
        now = _now()
        problem_items = [(p.text, p.severity) for p in feedback.problems]
        for m in feedback.missing_products:
            problem_items.append((f"missing {m.name}", "high"))
        for text, severity in problem_items:
            canon, _created = self.matcher.resolve(self.business_id, text, now)
            self.repo.link_feedback_problem(saved["id"], canon["id"], text, severity)

    async def _persist_and_notify(
        self,
        envelope: DecisionEnvelope,
        feedback: Feedback,
        customer: dict[str, Any] | None = None,
        *,
        call_id: int | None = None,
    ) -> None:
        decision = envelope.decision
        if decision.insight_title:
            self.repo.save_insight(
                self.business_id,
                insight_type=decision.insight_type,
                title=decision.insight_title,
                description=decision.insight_description,
                priority=decision.alert_priority,
                recommended_action=decision.recommended_action,
            )
        if envelope.action == "alert" and decision.alert and decision.alert_message:
            # Idempotent outbound alarm: at most one alarm per call, even if the
            # same call result is processed twice.
            key = f"alarm:{call_id or 'unknown'}"
            if self.repo.record_notification(call_id, "alarm", key):
                await notify.send_admin(decision.alert_message)
                self.repo.update_notification_status(key, "sent")

        # Human-approved compensation: only PROPOSE, never auto-apply. Gated on
        # verified negative evidence (a claim that passed verification).
        has_verified_negative = any(
            v.status == "verified" for v in envelope.verification
        )
        if (
            customer
            and feedback.sentiment == "negative"
            and not feedback.missing_products
            and customer.get("loyalty_status") in ("loyal", "vip")
            and has_verified_negative
        ):
            action = self.repo.create_action(
                self.business_id, "compensation_offer", proposed_by="agent",
                customer_id=customer["id"],
            )
            await notify.send_approval(
                f"💡 Müşteri {customer['phone']} yüksek sadakat gösteriyor ve olumsuz "
                f"geri bildirim verdi. Telafi teklifi düşünülebilir.\n"
                f"Bir oran seçin (yalnızca öneri kaydedilir; otomatik uygulanmaz):",
                action["id"],
            )

    # -- nightly synthesis ----------------------------------------------
    async def nightly_synthesis(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        local_now = self._business_local_now()
        day_key = (local_now - timedelta(days=1)).date().isoformat()

        # Idempotency: never produce two summaries for the same business day.
        if self.repo.daily_summary_exists(self.business_id, day_key):
            return []

        start = (now - timedelta(days=1)).isoformat(timespec="seconds")
        problems = self.repo.list_canonical_problems(self.business_id)
        lines: list[str] = []

        # Skip when there is genuinely no data to summarize.
        if not problems and not self.repo.list_feedbacks(self.business_id, limit=1):
            return []

        for p in problems:
            total = p["count"] or 0
            if total < self.settings.recurring_threshold:
                continue
            recent = self.repo.count_problem_mentions_since(
                self.business_id, p["id"], (now - timedelta(days=7)).isoformat(timespec="seconds")
            )
            prev_total = self.repo.count_problem_mentions_since(
                self.business_id, p["id"], (now - timedelta(days=14)).isoformat(timespec="seconds")
            )
            prev = prev_total - recent
            status, direction = classify_problem(
                total, recent, prev, self._days_ago(p.get("last_seen_at"))
            )
            affected = self.repo.count_affected_customers(p["id"])
            self.repo.update_canonical_status(p["id"], status)
            lines.append(
                f"- {p['name']}: {status} ({direction}, son 7g {recent}, "
                f"önceki 7g {prev}, {affected} farklı müşteri)"
            )

        avg = self.repo.average_satisfaction_since(self.business_id, start)
        description = "Son 24 saat içgörüsü.\n" + "\n".join(lines[:10])
        if avg is not None:
            description += f"\nGenel memnuniyet: {avg:.1f}/5"

        foreign_trend = self.compute_foreign_customer_trend()
        if foreign_trend["trend"] == "insufficient_data":
            description += "\nFarklı dil konuşan müşteri memnuniyeti: yetersiz veri"
        else:
            description += (
                f"\nFarklı dil konuşan müşteri memnuniyeti: {foreign_trend['trend']} "
                f"({foreign_trend['percent_change']:+.1f}%)"
            )

        insight = self.repo.save_insight(
            self.business_id,
            insight_type="daily_summary",
            title=f"{self.business_name} — Günlük özet",
            description=description,
            priority="medium",
            recommended_action="Tekrarlayan sorunlar için süreçleri gözden geçirin.",
            period_start=day_key,
            period_end=now.isoformat(timespec="seconds"),
        )
        return [insight]

    def compute_foreign_customer_trend(self, days: int = 7) -> dict[str, Any]:
        """GÖREV C: real, data-derived satisfaction trend for customers whose
        detected `preferred_language` (GÖREV 1) differs from
        `BUSINESS_DEFAULT_LOCALE`, comparing the current `days`-day window to
        the immediately preceding one. Never fabricates a number: below
        `min_samples` (3) in either window, returns "insufficient_data" and
        `percent_change=None` (see trends.classify_satisfaction_trend).
        """
        now = datetime.now(timezone.utc)
        recent_since = (now - timedelta(days=days)).isoformat(timespec="seconds")
        prev_since = (now - timedelta(days=2 * days)).isoformat(timespec="seconds")
        default_locale = self.settings.business_default_locale

        recent = self.repo.foreign_customer_satisfaction_since(
            self.business_id, default_locale, recent_since
        )
        prev = self.repo.foreign_customer_satisfaction_since(
            self.business_id, default_locale, prev_since, recent_since
        )
        label, change = classify_satisfaction_trend(
            prev["avg_satisfaction"], recent["avg_satisfaction"], prev["n"], recent["n"],
        )
        return {
            "trend": label,
            "percent_change": change,
            "recent_avg_satisfaction": recent["avg_satisfaction"],
            "prev_avg_satisfaction": prev["avg_satisfaction"],
            "recent_n": recent["n"],
            "prev_n": prev["n"],
            "window_days": days,
        }

    async def deliver_pending_synthesis(self) -> int:
        """Send any daily_summary insight(s) still pending delivery
        (`notified=0`) to Telegram, then mark each delivered.

        Separated from `nightly_synthesis` on purpose (GÖREV B): computation
        stays on the existing close_hour+1 schedule; delivery runs on its own
        schedule (`businesses.morning_summary_hour`). Iterates ALL pending
        entries (oldest first), not just the latest, so a missed delivery
        window never silently drops a night's summary.
        """
        pending = self.repo.list_undelivered_insights(self.business_id, "daily_summary")
        delivered = 0
        for insight in pending:
            await notify.send_admin(
                f"☀️ SmartFeedback — Günlük özet\n{insight['description']}"
            )
            if self.repo.mark_insight_notified(insight["id"]):
                delivered += 1
        return delivered

    # -- re-engagement outreach (GÖREV 2) --------------------------------
    #
    # Fully separate from the core feedback pipeline: never dispatches a
    # CALL-E call on its own. It only ever *proposes* a personalized,
    # LLM-generated message for human approval in Telegram; the actual call
    # is placed exclusively by `_dispatch_reengagement_call`, which runs only
    # after an explicit approval (see `telegram_bot.py`).
    async def propose_reengagement_outreach(self) -> int:
        """Find customers who have gone quiet (> DORMANT_DAYS since their
        last order) and have at least one previously verified complaint, and
        — at most once per dormancy episode — propose a personalized
        re-engagement message for human approval. Never calls anyone here."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=DORMANT_DAYS)
        ).isoformat(timespec="seconds")
        candidates = self.repo.list_dormant_customers(self.business_id, cutoff)
        proposed = 0
        for customer in candidates:
            if self.repo.has_action_since(
                customer["id"], "reengagement_outreach", customer.get("last_order_at")
            ):
                continue
            problems = self.repo.customer_verified_problems(
                self.business_id,
                customer["id"],
                min_confidence=self.settings.context_min_confidence,
                limit=self.settings.context_max_items,
            )
            if not problems:
                continue  # no verified complaint on record -> not a candidate
            names = ", ".join(p["name"] for p in problems)
            language = (
                customer.get("preferred_language")
                or self.settings.business_default_locale
            )
            message = self.calle.build_reengagement_task(
                self.business_name, language, names
            )
            if not message:
                # No LLM available (or it failed): skip rather than propose a
                # generic, non-personalized message for something that is
                # explicitly supposed to be personal.
                continue
            action = self.repo.create_action(
                self.business_id, "reengagement_outreach", proposed_by="agent",
                reason=message, customer_id=customer["id"],
            )
            await notify.send_simple_approval(
                f"👋 Müşteri {customer['phone']} {DORMANT_DAYS}+ gündür sipariş "
                f"vermedi ve daha önce doğrulanmış bir sorunu vardı ({names}).\n\n"
                f"Önerilen arama mesajı:\n\"{message}\"\n\n"
                f"Bu müşteriyi bu mesajla aramamı onaylıyor musunuz?",
                action["id"],
            )
            proposed += 1
        return proposed

    async def _dispatch_reengagement_call(
        self, action: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Place the CALL-E call for an APPROVED re-engagement outreach
        action. Idempotent per action (outreach_calls.action_id UNIQUE);
        never triggered automatically — only from an explicit Telegram
        approval. Kept fully separate from `_dispatch_call` (the core
        post-delivery feedback call)."""
        if action.get("status") != "resolved":
            return None
        customer_id = action.get("customer_id")
        if not customer_id:
            return None
        customer = self._load_customer(customer_id)
        if customer is None or customer["do_not_call"]:
            return None
        try:
            outreach = self.repo.create_outreach_call(action["id"], customer_id)
        except sqlite3.IntegrityError:
            return self.repo.get_outreach_call_by_action(action["id"])
        if not self.calle.enabled:
            self.repo.update_outreach_call(
                outreach["id"], status="failed",
                failure_code="calle_not_configured", completed_at=_now(),
            )
            return self.repo.get_outreach_call_by_action(action["id"])

        task = action.get("reason") or self.calle.build_reengagement_task(
            self.business_name,
            customer.get("preferred_language") or self.settings.business_default_locale,
            "",
        )
        try:
            result = self.calle.place_action_call(
                phone=customer["phone"],
                task=task or "",
                action_id=action["id"],
                business_id=self.business_id,
                environment=self.settings.environment,
                preferred_language=customer.get("preferred_language"),
            )
            self.repo.update_outreach_call(
                outreach["id"], calle_call_id=str(result.get("id"))
            )
        except Exception as exc:  # noqa: BLE001
            self.repo.update_outreach_call(
                outreach["id"], status="failed", failure_code=str(exc)[:200],
                completed_at=_now(),
            )
        return self.repo.get_outreach_call_by_action(action["id"])

    # -- frequency-drop risk detection (GÖREV F) -------------------------
    #
    # Independent of GÖREV 2's dormant-customer check: that one is a fixed
    # "N+ days since last order AND a past verified complaint" rule; this one
    # compares a customer's time-since-last-order against THEIR OWN
    # historical average ordering interval (a per-customer, ratio-based
    # signal), and never requires a prior complaint. Deliberately NOT merged
    # with `propose_reengagement_outreach` — the two run independently, and
    # if both fire for the same customer on the same day, the business owner
    # gets two separate approval requests (never silently combined).
    #
    # Reuses the EXISTING `compensation_offer` action_type and percentage-
    # button approval flow verbatim (same `notify.send_approval`, same
    # `_dispatch_offer_call` / `outreach_calls` idempotency from GÖREV 3) —
    # only the detection logic and the proposal message are new.
    async def detect_frequency_drop_risk(self) -> int:
        """Flag customers whose time-since-last-order has grown to at least
        `settings.frequency_drop_ratio_threshold`x their own historical
        average ordering interval, and propose a compensation offer for
        human approval (never applied/called automatically).

        Requires >= `settings.frequency_drop_min_orders` past orders to
        compute a customer's "normal rhythm" at all; below that, the honest
        result is insufficient_data — no guess is ever made for a new
        customer with too little history.
        """
        proposed = 0
        for customer in self.repo.list_customers(self.business_id):
            dates = self.repo.customer_order_dates(
                customer["id"], limit=self.settings.frequency_drop_min_orders * 2 + 2
            )
            rhythm = compute_customer_rhythm(
                dates, min_orders=self.settings.frequency_drop_min_orders
            )
            if rhythm["status"] == "insufficient_data":
                continue
            if rhythm["ratio"] is None or rhythm["ratio"] < self.settings.frequency_drop_ratio_threshold:
                continue
            if self.repo.has_action_since(
                customer["id"], "compensation_offer", customer.get("last_order_at")
            ):
                continue

            history = self.repo.customer_complaint_history_by_phone(
                self.business_id, customer["phone"]
            )
            names = ", ".join(h["name"] for h in history)
            action = self.repo.create_action(
                self.business_id, "compensation_offer", proposed_by="agent",
                customer_id=customer["id"],
                reason=(
                    f"frequency_drop_risk: normal rhythm ~{rhythm['avg_interval_days']}g, "
                    f"{rhythm['days_since_last']}g geçti ({rhythm['ratio']}x)"
                ),
            )
            history_line = f"\nGeçmiş doğrulanmış şikayetler: {names}" if names else ""
            await notify.send_approval(
                f"📉 Müşteri {customer['phone']} sipariş sıklığı düştü: normal ritmi "
                f"~{rhythm['avg_interval_days']:.0f} gün, şu an {rhythm['days_since_last']:.0f} "
                f"gün geçti (~{rhythm['ratio']:.1f}x)." + history_line +
                "\nBu müşteriye bir indirim/tazminat teklifi önerilsin mi? "
                "Bir oran seçin (yalnızca öneri kaydedilir; otomatik uygulanmaz):",
                action["id"],
            )
            proposed += 1
        return proposed

    # -- compensation-offer call (GÖREV 3) -------------------------------
    #
    # `app/actions.py::execute_approved_action` (the sandbox webhook) is
    # UNCHANGED and still fires independently. This is a SEPARATE, additional
    # call flow: it actually notifies the customer by phone once a human has
    # approved a compensation offer. It is never triggered automatically —
    # only from an explicit Telegram approval — and it is kept fully apart
    # from `_dispatch_call` (order-keyed) and from the sandbox executor.
    async def _dispatch_offer_call(
        self, action: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Place the CALL-E call informing a customer that their compensation
        offer was approved. Idempotent per action
        (outreach_calls.action_id UNIQUE, exactly like `_dispatch_reengagement_call`)."""
        if action.get("status") != "resolved":
            return None
        decision = action.get("decision") or ""
        if not decision.startswith("approved"):
            return None
        customer_id = action.get("customer_id")
        if not customer_id:
            return None
        customer = self._load_customer(customer_id)
        if customer is None or customer["do_not_call"]:
            return None
        percentage = _extract_percentage(decision)
        if percentage is None:
            return None
        try:
            outreach = self.repo.create_outreach_call(action["id"], customer_id)
        except sqlite3.IntegrityError:
            return self.repo.get_outreach_call_by_action(action["id"])
        if not self.calle.enabled:
            self.repo.update_outreach_call(
                outreach["id"], status="failed",
                failure_code="calle_not_configured", completed_at=_now(),
            )
            return self.repo.get_outreach_call_by_action(action["id"])

        language = customer.get("preferred_language") or self.settings.business_default_locale
        task = self.calle.build_offer_call_task(self.business_name, language, percentage)
        try:
            result = self.calle.place_action_call(
                phone=customer["phone"],
                task=task,
                action_id=action["id"],
                business_id=self.business_id,
                environment=self.settings.environment,
                preferred_language=customer.get("preferred_language"),
            )
            self.repo.update_outreach_call(
                outreach["id"], calle_call_id=str(result.get("id"))
            )
        except Exception as exc:  # noqa: BLE001
            self.repo.update_outreach_call(
                outreach["id"], status="failed", failure_code=str(exc)[:200],
                completed_at=_now(),
            )
        return self.repo.get_outreach_call_by_action(action["id"])

    def _business_local_now(self) -> datetime:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(self.settings.business_timezone))
        except Exception:  # noqa: BLE001
            return datetime.now(timezone.utc)

    @staticmethod
    def _days_ago(iso: str | None) -> float | None:
        if not iso:
            return None
        try:
            ts = datetime.fromisoformat(iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        except (ValueError, TypeError):
            return None

    # -- natural-language query -----------------------------------------
    async def answer_query(self, question: str) -> str:
        ctx = self._build_query_context()
        if self._llm is not None:
            result = complete_json(
                self._llm,
                "You answer the business owner's questions about customer feedback "
                "using only the provided data. Return JSON {\"answer\": \"...\"}.",
                f"Data:\n{ctx}\n\nQuestion: {question}",
            )
            if result and result.get("answer"):
                return result["answer"]
        return ctx

    def _build_query_context(self) -> str:
        problems = self.repo.list_canonical_problems(self.business_id)
        insights = self.repo.list_insights(self.business_id, limit=20)
        feedbacks = self.repo.list_feedbacks(self.business_id, limit=50)
        parts = ["Recent problems:"]
        for p in problems[:15]:
            parts.append(f"- {p['name']} (count={p['count']}, status={p['status']})")
        parts.append("\nRecent insights:")
        for i in insights[:10]:
            parts.append(f"- {i['title']}: {i['description']}")
        parts.append(f"\nTotal feedbacks in window: {len(feedbacks)}")
        return "\n".join(parts)

    def _load_customer(self, customer_id: int) -> dict[str, Any] | None:
        with _conn(self.repo.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
            return dict(row) if row else None
