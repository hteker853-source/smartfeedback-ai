from datetime import datetime, timezone

from app.canonical import CanonicalMatcher
from app.repository import Repository


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def test_similar_problems_map_to_same_canonical(repo: Repository, settings):
    matcher = CanonicalMatcher(repo, settings)
    b = repo.get_or_create_business("Test Business")

    c1, created1 = matcher.resolve(b["id"], "patatesler soğuk geldi", _now())
    c2, created2 = matcher.resolve(b["id"], "patatesler sıcak değildi", _now())
    c3, created3 = matcher.resolve(b["id"], "patates soğuk", _now())

    assert created1 is True
    assert c2["id"] == c1["id"]
    assert c3["id"] == c1["id"]
    # count accumulated
    fresh = repo.get_canonical_problem(b["id"], c1["name"])
    assert fresh["count"] >= 3


def test_different_problem_creates_new_canonical(repo: Repository, settings):
    matcher = CanonicalMatcher(repo, settings)
    b = repo.get_or_create_business("Test Business")
    c1, _ = matcher.resolve(b["id"], "patatesler soğuk", _now())
    c2, created = matcher.resolve(b["id"], "teslimat çok geçti", _now())
    assert created is True
    assert c2["id"] != c1["id"]


def test_vector_roundtrip(repo: Repository, settings):
    from app import vector

    v = vector.embed("patatesler soğuk geldi")
    assert len(v) == vector.DIM
    assert abs(sum(x * x for x in v) - 1.0) < 1e-6
    sim = vector.cosine(vector.embed("patatesler soğuk"), vector.embed("patates soğuk geldi"))
    assert sim > 0.5


def test_match_reports_confidence_and_method(repo: Repository, settings):
    matcher = CanonicalMatcher(repo, settings)
    b = repo.get_or_create_business("Test Business")
    matcher.resolve(b["id"], "patatesler soğuk geldi", _now())

    m = matcher.match(b["id"], "patatesler soğuktu")
    assert m["id"] is not None
    assert m["match_method"] == "vector"
    assert m["match_confidence"] >= 0.55
    assert "ambiguous" in m


def test_match_fallback_for_unknown_phrase(repo: Repository, settings):
    matcher = CanonicalMatcher(repo, settings)
    b = repo.get_or_create_business("Test Business")
    m = matcher.match(b["id"], "bambaşka bir şikayet cümlesi")
    assert m["id"] is None
    assert m["match_method"] == "fallback"
    assert m["match_confidence"] == 0.0

