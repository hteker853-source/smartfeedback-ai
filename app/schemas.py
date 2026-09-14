"""Pydantic schemas for structured analysis outputs and API payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Sentiment = Literal["positive", "neutral", "negative"]
Priority = Literal["critical", "high", "medium", "low"]
Urgency = Literal["critical", "high", "medium", "low"]
Language = Literal["tr", "en", "de"]
ProblemStatus = Literal["new", "active", "improving", "likely_resolved", "insufficient_data"]
OrderStatus = Literal["created", "sent", "completed", "cancelled"]
CallStatus = Literal["scheduled", "calling", "completed", "failed", "no_answer", "do_not_call"]

# Evidence-bound decision types.
ClaimKind = Literal["problem", "missing_item", "positive"]
VerificationStatus = Literal["verified", "unsupported", "unverifiable", "contradicted"]
DecisionAction = Literal["no_action", "record", "alert", "human_approval", "blocked"]
CaseStatus = Literal[
    "opened",
    "evidence_collected",
    "verified",
    "decision_ready",
    "alerted",
    "resolved",
]


class MissingProduct(BaseModel):
    name: str
    urgency: Urgency = "high"
    notify: bool = True


class Problem(BaseModel):
    text: str
    category: str = "general"
    severity: Priority = "medium"


class Feedback(BaseModel):
    """Structured result of understanding a single customer transcript."""

    satisfaction: int | None = Field(default=None, ge=1, le=5)
    sentiment: Sentiment = "neutral"
    positives: list[str] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
    category: str = "general"
    priority: Priority = "medium"
    urgency: Urgency = "low"
    missing_products: list[MissingProduct] = Field(default_factory=list)
    recommended_action: str = ""
    language: Language = "tr"
    summary: str = ""
    reasoning_summary: str = Field(
        default="",
        description=(
            "One concise, factual sentence in your own words describing what "
            "you actually found in THIS transcript (e.g. 'Customer reported "
            "cold fries and a missing drink, overall negative'). Not a "
            "template — describe this specific call."
        ),
    )


class LanguageDetection(BaseModel):
    """LLM-detected spoken language of a transcript, open-ended (not limited
    to the deterministic analyzer's tr/en/de set). Used only to steer future
    customer-facing message generation, never the deterministic analysis."""

    language: str = "und"  # ISO 639-1 code, or "und" if undetermined


class InvestigationReport(BaseModel):
    """Evidence gathered from historical/structured data."""

    recurring_problems: list[str] = Field(default_factory=list)
    relevant_history: str = ""
    customer_order_count: int = 0
    loyalty_status: str = "new"
    churn_risk: str = "none"
    reasoning_summary: str = Field(
        default="",
        description=(
            "One concise, factual sentence describing what YOU actually found "
            "via the tools (e.g. 'Found 2 prior complaints about late delivery "
            "from this customer in the last 30 days'). Not a template — state "
            "what the tool results actually showed, or that nothing relevant "
            "was found."
        ),
    )


class CustomerTrendReport(BaseModel):
    trends: list[str] = Field(default_factory=list)
    improving_problems: list[str] = Field(default_factory=list)
    rising_problems: list[str] = Field(default_factory=list)
    overall_satisfaction: float | None = None
    reasoning_summary: str = Field(
        default="",
        description=(
            "One concise, factual sentence describing the loyalty/churn/trend "
            "signal YOU actually found (e.g. 'Customer is loyal with no rising "
            "problems detected'). Not a template."
        ),
    )


class Decision(BaseModel):
    """Supervisor decision synthesis."""

    alert: bool = False
    alert_priority: Priority = "medium"
    alert_message: str = ""
    insight_title: str = ""
    insight_type: str = "feedback"
    insight_description: str = ""
    recommended_action: str = ""
    reasoning_summary: str = Field(
        default="",
        description=(
            "One concise, factual sentence explaining WHY you are proposing "
            "this outcome, reconciling the three specialist reports (e.g. "
            "'Escalating because the missing item was corroborated by both "
            "the feedback and investigation reports'). Not a template."
        ),
    )


class TranscriptSpan(BaseModel):
    """A concrete excerpt of the transcript backing a claim."""

    start: int
    end: int
    text: str


class Claim(BaseModel):
    """A single evidence-bound claim extracted from a transcript.

    Every claim carries a reference to the transcript span (and, when available,
    tool evidence) that supports it. This is the unit the verification layer and
    the deterministic decision gate operate on.
    """

    id: str
    text: str
    kind: ClaimKind = "problem"
    category: str = "general"
    severity: Priority = "medium"
    confidence: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    transcript_spans: list[TranscriptSpan] = Field(default_factory=list)
    source_agent: str = "deterministic"
    model_used: str = ""


class VerificationResult(BaseModel):
    """Deterministic verification of a single claim against the transcript."""

    claim_id: str
    status: VerificationStatus = "unverifiable"
    span_found: bool = False
    supported: bool = False
    reason: str = ""


class ConfidenceBreakdown(BaseModel):
    """Deterministic decomposition of a confidence score.

    The total is not an LLM-generated number: it is computed from evidence
    quality, signal strength, sample size and verification results.
    """

    evidence_quality: float = 0.0
    signal_strength: float = 0.0
    sample_size: float = 0.0
    verification_rate: float = 0.0
    total: float = 0.0


class DissentEntry(BaseModel):
    """A structured record of disagreement between an agent finding and the
    final decision. No free-form agent chat; just structured state."""

    source_agent: str = ""
    finding: str = ""
    reason: str = ""
    affected_decision: bool = False


class ModelProvenance(BaseModel):
    """Provenance of an important LLM output."""

    provider: str = ""
    model: str = ""
    fallback_occurred: bool = False
    latency_ms: float | None = None
    correlation_id: str = ""
    timestamp: str = ""


class ImpactScore(BaseModel):
    """Deterministic business-impact score (never sentiment, never an LLM guess)."""

    score: float = 0.0
    label: str = "low"
    recurrence: float = 0.0
    severity: float = 0.0
    affected_customers: float = 0.0
    trend: float = 0.0
    recency: float = 0.0


class DecisionEnvelope(BaseModel):
    """The full, replayable decision trace for one call.

    Holds the raw decision together with its evidence, verification, confidence,
    dissent and provenance so that "why did this alarm fire?" can be answered
    from the system's own data.
    """

    decision: Decision
    action: DecisionAction = "record"
    confidence: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    verification: list[VerificationResult] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    dissent: list[DissentEntry] = Field(default_factory=list)
    provenance: ModelProvenance | None = None
    case_id: int | None = None
    dnc_requested: bool = False
    impact: ImpactScore | None = None
    trend_direction: str = "unknown"
    trend_confidence: float | None = None
