"""Evidence-bound claim extraction, verification and confidence.

This is the deterministic backbone of the "claim -> evidence -> verification ->
confidence" chain. It turns the structured `Feedback` (from either the Strands
Feedback agent or the deterministic analyzer) into evidence-bound `Claim`
objects, verifies each claim against the raw transcript, and computes a
confidence score that is never an opaque LLM number.

The LLM is free to *understand* the transcript; the code here answers
"can this claim be backed by an actual span of the transcript?".
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

from .analyzer import has_missing_signal, has_negated_missing_signal
from .schemas import (
    Claim,
    ConfidenceBreakdown,
    Feedback,
    MissingProduct,
    Problem,
    TranscriptSpan,
    VerificationResult,
)

# Signal strength: how many distinct negative/absence signals we need before we
# consider the evidence "strong". Kept low and documented (not magic): a single
# clearly-missing item already counts as one strong signal.
_SPAN_WINDOW = 60


def _claim_id(kind: str, text: str) -> str:
    digest = hashlib.sha256(f"{kind}:{text.strip().lower()}".encode("utf-8")).hexdigest()
    return digest[:16]


def _find_span(transcript: str, term: str, window: int = _SPAN_WINDOW) -> TranscriptSpan | None:
    """Locate `term` in the transcript (case-insensitive) and return a windowed span."""
    term = (term or "").strip()
    if not term:
        return None
    idx = transcript.lower().find(term.lower())
    if idx == -1:
        return None
    start = max(0, idx - window)
    end = min(len(transcript), idx + len(term) + window)
    return TranscriptSpan(start=start, end=end, text=transcript[start:end])


def _has_missing_marker(transcript: str, locale: str) -> bool:
    """A missing-item claim is only *supported* when the transcript carries a
    non-negated absence signal (see analyzer.has_missing_signal)."""
    return has_missing_signal(transcript, locale)


def _claim_from_problem(problem: Problem, transcript: str) -> Claim:
    span = _find_span(transcript, problem.text)
    return Claim(
        id=_claim_id("problem", problem.text),
        text=problem.text,
        kind="problem",
        category=problem.category,
        severity=problem.severity,
        evidence_refs=[span.text] if span else [],
        transcript_spans=[span] if span else [],
        source_agent="deterministic",
    )


def _claim_from_missing(missing: MissingProduct, transcript: str) -> Claim:
    span = _find_span(transcript, missing.name)
    return Claim(
        id=_claim_id("missing_item", missing.name),
        text=f"missing {missing.name}",
        kind="missing_item",
        category="product",
        severity="high",
        evidence_refs=[span.text] if span else [],
        transcript_spans=[span] if span else [],
        source_agent="deterministic",
    )


def extract_claims(feedback: Feedback, transcript: str, locale: str = "tr") -> list[Claim]:
    """Bind every problem / missing item in `feedback` to a transcript span."""
    claims: list[Claim] = []
    for problem in feedback.problems:
        claims.append(_claim_from_problem(problem, transcript))
    for missing in feedback.missing_products:
        claims.append(_claim_from_missing(missing, transcript))
    return claims


def verify_claim(claim: Claim, transcript: str, locale: str = "tr") -> VerificationResult:
    """Deterministic verification of one claim.

    Layers (deterministic, run before any LLM semantic pass):

    1. Span existence: is the referenced evidence actually present?
    2. Support / contradiction: does the transcript carry a *non-negated*
       signal that supports the claim (SUPPORTS), an explicit negation that
       reverses it (CONTRADICTS), or nothing (INSUFFICIENT)?
    """
    span_found = any(_find_span(transcript, ref) is not None for ref in claim.evidence_refs) or (
        any(s.start >= 0 and s.text for s in claim.transcript_spans)
        and any(s.text.strip() in transcript for s in claim.transcript_spans)
    )

    if not span_found and not claim.evidence_refs:
        return VerificationResult(
            claim_id=claim.id,
            status="unverifiable",
            span_found=False,
            supported=False,
            reason="claim has no transcript evidence",
        )

    if claim.kind == "missing_item":
        supported = _has_missing_marker(transcript, locale)
        contradicted = has_negated_missing_signal(transcript, locale)
    else:
        supported = span_found
        contradicted = False

    if not span_found:
        return VerificationResult(
            claim_id=claim.id,
            status="unverifiable",
            span_found=False,
            supported=False,
            reason="referenced span not found in transcript",
        )
    if contradicted:
        return VerificationResult(
            claim_id=claim.id,
            status="contradicted",
            span_found=True,
            supported=False,
            reason="transcript negates the claim (e.g. 'not missing')",
        )
    if not supported:
        return VerificationResult(
            claim_id=claim.id,
            status="unsupported",
            span_found=True,
            supported=False,
            reason="span present but no supporting absence/negation signal",
        )
    return VerificationResult(
        claim_id=claim.id,
        status="verified",
        span_found=True,
        supported=True,
        reason="span present and supported",
    )


def verify_claims(claims: Iterable[Claim], transcript: str, locale: str = "tr") -> list[VerificationResult]:
    return [verify_claim(c, transcript, locale) for c in claims]


def _claim_confidence(verification: VerificationResult, n_claims: int) -> float:
    """Deterministic per-claim confidence from verification outcome only."""
    if verification.status == "verified":
        # More verified claims that agree on the same signal -> stronger.
        return min(1.0, 0.5 + 0.1 * max(0, n_claims - 1))
    if verification.status == "unsupported":
        return 0.3
    return 0.15


def aggregate_confidence(
    claims: list[Claim],
    verifications: list[VerificationResult],
    occurrence_count: int,
    *,
    signal_strength: float | None = None,
) -> ConfidenceBreakdown:
    """Compute a decomposed confidence score from deterministic signals.

    Components:
      - evidence_quality: mean per-claim confidence (span + support).
      - signal_strength: how many distinct supporting signals were found
        (or an explicit override when provided).
      - sample_size: recurrence across history (diminishing returns via log).
      - verification_rate: fraction of claims that verified.
    """
    n = len(verifications)
    if n == 0:
        return ConfidenceBreakdown()

    verified = [v for v in verifications if v.status == "verified"]
    verification_rate = len(verified) / n

    evidence_quality = sum(_claim_confidence(v, n) for v in verifications) / n

    if signal_strength is None:
        # One explicit missing/absence signal per verified claim, capped.
        signal_strength = min(1.0, len(verified) * 0.5)

    sample_size = 0.0
    if occurrence_count and occurrence_count > 1:
        sample_size = min(1.0, 0.35 * math.log2(1 + occurrence_count))

    # Weighted blend, all components deterministic and explainable.
    total = (
        0.45 * evidence_quality
        + 0.25 * verification_rate
        + 0.20 * signal_strength
        + 0.10 * sample_size
    )
    total = round(min(1.0, max(0.0, total)), 3)

    return ConfidenceBreakdown(
        evidence_quality=round(evidence_quality, 3),
        signal_strength=round(signal_strength, 3),
        sample_size=round(sample_size, 3),
        verification_rate=round(verification_rate, 3),
        total=total,
    )
