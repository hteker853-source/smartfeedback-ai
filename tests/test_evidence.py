"""Tests for evidence-bound claim extraction, verification and confidence."""

from app import evidence
from app.schemas import Claim, Feedback, MissingProduct, Problem


def _feedback(problems=None, missing=None):
    return Feedback(
        problems=problems or [],
        missing_products=missing or [],
    )


def test_extract_claims_binds_spans():
    fb = Feedback(
        problems=[Problem(text="soğuk", category="quality", severity="medium")],
        missing_products=[MissingProduct(name="ayran", urgency="high", notify=True)],
    )
    claims = evidence.extract_claims(fb, "Yemek güzeldi ama patatesler soğuk geldi, ayran gelmedi.", "tr")
    assert len(claims) == 2
    kinds = {c.kind for c in claims}
    assert kinds == {"problem", "missing_item"}
    for c in claims:
        assert c.transcript_spans, f"claim {c.text} should have a span"


def test_verify_claim_missing_item_supported():
    fb = Feedback(missing_products=[MissingProduct(name="ayran")])
    claims = evidence.extract_claims(fb, "Ayran sipariş etmiştik ama gelmedi.", "tr")
    result = evidence.verify_claim(claims[0], "Ayran sipariş etmiştik ama gelmedi.", "tr")
    assert result.status == "verified"
    assert result.span_found is True
    assert result.supported is True


def test_verify_claim_unverifiable_when_span_absent():
    claim = Claim(
        id="x",
        text="missing gizmo",
        kind="missing_item",
        evidence_refs=["no such text in transcript"],
    )
    result = evidence.verify_claim(claim, "Tamamen farklı bir içerik.", "tr")
    assert result.status == "unverifiable"
    assert result.span_found is False


def test_verify_claim_unsupported_when_no_missing_marker():
    claim = Claim(
        id="x",
        text="missing ayran",
        kind="missing_item",
        evidence_refs=["ayran çok güzeldi"],
    )
    result = evidence.verify_claim(claim, "ayran çok güzeldi", "tr")
    assert result.status == "unsupported"
    assert result.span_found is True
    assert result.supported is False


def test_verify_claim_no_evidence_is_unverifiable():
    claim = Claim(id="x", text="problem", kind="problem")
    result = evidence.verify_claim(claim, "some transcript", "tr")
    assert result.status == "unverifiable"
    assert "no transcript evidence" in result.reason


def test_aggregate_confidence_bounds_and_monotonic():
    fb = Feedback(
        problems=[Problem(text="soğuk")],
        missing_products=[MissingProduct(name="ayran")],
    )
    claims = evidence.extract_claims(fb, "patatesler soğuk geldi, ayran gelmedi.", "tr")
    verifications = evidence.verify_claims(claims, "patatesler soğuk geldi, ayran gelmedi.", "tr")

    low = evidence.aggregate_confidence(claims, verifications, occurrence_count=1)
    high = evidence.aggregate_confidence(claims, verifications, occurrence_count=30)

    assert 0.0 <= low.total <= 1.0
    assert 0.0 <= high.total <= 1.0
    # Recurrence must raise sample_size and total confidence.
    assert high.sample_size > low.sample_size
    assert high.total >= low.total


def test_aggregate_confidence_empty():
    conf = evidence.aggregate_confidence([], [], 0)
    assert conf.total == 0.0
    assert conf.evidence_quality == 0.0


def test_verify_negated_missing_claim_contradicted():
    claim = Claim(id="x", text="missing ürün", kind="missing_item", evidence_refs=["ürün"])
    result = evidence.verify_claim(claim, "ürün eksik değildi, sadece sos ayrı gelmiş", "tr")
    assert result.status == "contradicted"
    assert result.supported is False


def test_verify_real_missing_claim_verified():
    claim = Claim(id="x", text="missing ayran", kind="missing_item", evidence_refs=["ayran"])
    result = evidence.verify_claim(claim, "ayran gelmedi", "tr")
    assert result.status == "verified"
    assert result.supported is True

