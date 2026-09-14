"""Deterministic decision gate.

The LLM (supervisor) *proposes* an outcome; this layer *decides* whether the
proposal is allowed and what actually happens. It is pure, deterministic Python:
no LLM call decides a consequential action (alert, compensation proposal, …).

The gate classifies each call into one of:

- ``no_action``        — nothing worth recording / acting on.
- ``record``           — record an insight, no alert.
- ``alert``            — verified high-signal evidence -> immediate Telegram alert.
- ``human_approval``   — a consequential (money-related) action, only ever proposed.
- ``blocked``          — the proposal is not allowed by policy (e.g. unverified).

A critical rule: **no alert / compensation action may be produced from an
unverified claim.** When a claim fails verification, the gate downgrades the
action and records structured ``dissent`` so the override is not silent.
"""

from __future__ import annotations

from .schemas import (
    Claim,
    ConfidenceBreakdown,
    Decision,
    DissentEntry,
    Feedback,
    ImpactScore,
    VerificationResult,
)


def _verified_set(verifications: list[VerificationResult]) -> set[str]:
    return {v.claim_id for v in verifications if v.status == "verified"}


def _dissent_failed(claims: list[Claim], verified: set[str]) -> list[DissentEntry]:
    dissent: list[DissentEntry] = []
    for c in claims:
        if c.id not in verified:
            dissent.append(
                DissentEntry(
                    source_agent="verification",
                    finding=c.text,
                    reason="claim not verified against transcript; action downgraded",
                    affected_decision=True,
                )
            )
    return dissent


def evaluate(
    feedback: Feedback,
    claims: list[Claim],
    verifications: list[VerificationResult],
    confidence: ConfidenceBreakdown,
    occurrence_by_claim: dict[str, int],
    *,
    min_alert_confidence: float,
    recurring_threshold: int,
    proposed_alert: bool = False,
    impact: ImpactScore | None = None,
) -> tuple[Decision, str, list[DissentEntry]]:
    """Return (decision, action, dissent) for one call.

    `proposed_alert` is the supervisor agent's (or fallback heuristic's) raw
    escalation intent; the gate may override it. `impact` (when supplied) makes
    the gate risk-adaptive: recurring, high-severity, high-impact operational
    defects may alert even without a missing item, while high-impact but
    uncertain cases escalate to human review instead of acting autonomously.
    """
    verified = _verified_set(verifications)
    dissent: list[DissentEntry] = []

    missing_claims = [c for c in claims if c.kind == "missing_item"]
    problem_claims = [c for c in claims if c.kind == "problem"]

    verified_missing = [c for c in missing_claims if c.id in verified]
    verified_problems = [c for c in problem_claims if c.id in verified]

    failed_claims = [c for c in claims if c.id not in verified]
    if failed_claims:
        dissent.extend(_dissent_failed(failed_claims, verified))

    max_occurrence = max((occurrence_by_claim.get(c.id, 0) for c in claims), default=0)
    recurring = [c.text for c in claims if occurrence_by_claim.get(c.id, 0) >= recurring_threshold]

    # --- Verified missing item: the only fully autonomous alert path. ---------
    if verified_missing:
        names = ", ".join(_display_name(c) for c in verified_missing)
        decision = Decision(
            alert=True,
            alert_priority="high" if len(verified_missing) < 2 else "critical",
            alert_message=(
                f"🚨 Acil müşteri sorunu\nEksik ürün: {names}\n"
                f"Müşteri geri bildirim görüşmesinde bildirildi ve kanıtla doğrulandı."
            ),
            insight_type="missing_item",
            insight_title="Eksik ürün bildirimi",
            insight_description=f"Müşteri şu ürün(ler)in eksik olduğunu belirtti: {names}.",
            recommended_action="Siparişi kontrol edin ve müşteriyle iletişime geçin.",
        )
        return decision, "alert", dissent

    # --- Proposed alert but no verified missing item -> downgrade. -------------
    if proposed_alert and failed_claims:
        dissent.append(
            DissentEntry(
                source_agent="policy",
                finding="alert proposed for unverified claim(s)",
                reason=f"confidence {confidence.total} below gate / evidence not verified",
                affected_decision=True,
            )
        )

    # --- Recurring, high-severity, high-impact operational defect -> alert. ----
    high_sev_problems = [c for c in verified_problems if c.severity in ("high", "critical")]
    if (
        impact is not None
        and impact.label == "high"
        and high_sev_problems
        and recurring
        and confidence.total >= min_alert_confidence
    ):
        joined = ", ".join(dict.fromkeys(_display_name(c) for c in high_sev_problems))
        decision = Decision(
            alert=True,
            alert_priority="high",
            alert_message=(
                f"🚨 Tekrarlayan operasyonel sorun\nSorun: {joined}\n"
                f"Kanıt doğrulandı; yüksek iş etkisi ve yükselen tekrar tespit edildi."
            ),
            insight_type="recurring_problem",
            insight_title="Tekrarlayan operasyonel sorun",
            insight_description=f"Yüksek etkili, tekrarlayan sorun: {joined}.",
            recommended_action="İlgili operasyonel süreci inceleyin.",
        )
        return decision, "alert", dissent

    # --- Verified recurring problem -> record (no alert, keep the human loop). -
    if recurring:
        joined = ", ".join(dict.fromkeys(_display_name(c) for c in claims if c.text in recurring))
        decision = Decision(
            alert=False,
            insight_type="recurring_problem",
            insight_title="Tekrarlayan sorun",
            insight_description=f"Tekrarlayan sorun adayı: {joined}.",
            recommended_action="İlgili süreci inceleyin.",
        )
        return decision, "record", dissent

    # --- High impact but uncertain / disputed -> human review, not autonomy. ---
    if (
        impact is not None
        and impact.label == "high"
        and claims
        and (confidence.total < min_alert_confidence or any(d.affected_decision for d in dissent))
    ):
        decision = Decision(
            alert=False,
            insight_type="review_required",
            insight_title="İnsan incelemesi gerekli",
            insight_description=(
                "Yüksek iş etkili sinyal tespit edildi ancak kanıt/doğrulama yetersiz; "
                "otomatik aksiyon alınmadı, insan incelemesi önerilir."
            ),
            recommended_action="Vakayı manuel inceleyin.",
        )
        return decision, "human_approval", dissent

    # --- Unverified missing-item intent (agent hallucinated an item). ----------
    if feedback.missing_products and not verified_missing:
        decision = Decision(
            alert=False,
            insight_type="unverified",
            insight_title="Doğrulanamayan geri bildirim",
            insight_description="Eksik ürün iddiası transkriptle doğrulanamadı; aksiyon alınmadı.",
            recommended_action="Transkripti gözden geçirin.",
        )
        return decision, "record", dissent

    # --- Verified problem (single occurrence) -> record. -----------------------
    if verified_problems or feedback.problems:
        decision = Decision(
            alert=False,
            insight_type="feedback",
            insight_title="Geri bildirim kaydedildi",
            insight_description=feedback.summary or "Geri bildirim alındı.",
            recommended_action=feedback.recommended_action or "",
        )
        return decision, "record", dissent

    # --- Nothing actionable. ----------------------------------------------------
    decision = Decision(
        alert=False,
        insight_type="feedback",
        insight_title="Geri bildirim kaydedildi",
        insight_description=feedback.summary or "Geri bildirim alındı.",
        recommended_action=feedback.recommended_action or "",
    )
    return decision, "no_action", dissent


def _display_name(claim: Claim) -> str:
    if claim.kind == "missing_item" and claim.text.startswith("missing "):
        return claim.text[len("missing "):]
    return claim.text

