"""Canonical problem matching.

Different phrasings ("cold fries", "fries were not hot") are mapped to the
same canonical problem. Matching uses a deterministic vector similarity in
SQLite; naming can be improved by an LLM (DeepSeek if present, else Bedrock)
but never blocks the pipeline.
"""

from __future__ import annotations

import json
from typing import Any

from . import vector
from .llm import complete_json, get_llm
from .repository import Repository

MATCH_THRESHOLD = 0.55
# If two canonical problems are this close in similarity, the match is ambiguous
# and should not be treated as a confident recurrence.
AMBIGUITY_DELTA = 0.05
_CANONICAL_PROMPT = (
    "You normalize customer complaints into short canonical problem labels. "
    "Return JSON only: {\"name\": \"<short lowercase label>\", \"category\": \"<category>\"}. "
    "Categories: product, delivery, service, quality, pricing, other."
)


def _load_vector(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, list) and all(isinstance(x, (int, float)) for x in value):
        return [float(x) for x in value]
    return None


class CanonicalMatcher:
    def __init__(self, repo: Repository, settings: Any) -> None:
        self.repo = repo
        self.settings = settings
        self._llm = get_llm(settings)

    def _canonicalize_name(self, problem_text: str) -> tuple[str, str]:
        default = vector.normalize(problem_text).strip() or problem_text.strip().lower()
        result = complete_json(
            self._llm, _CANONICAL_PROMPT, f"Complaint: {problem_text}"
        )
        if result and result.get("name"):
            return result["name"].strip().lower(), result.get("category", "general")
        return default, "general"

    def match(self, business_id: int, problem_text: str) -> dict[str, Any]:
        """Read-only: return the closest canonical problem (id/name/count) without
        incrementing counts. Also reports match confidence and ambiguity. Used by
        the agent's match_canonical_problem tool and the decision gate."""
        vec = vector.embed(problem_text)
        best: dict[str, Any] | None = None
        best_score = 0.0
        second_score = 0.0
        for p in self.repo.list_canonical_problems(business_id):
            pv = _load_vector(p.get("vector"))
            if pv is None:
                continue
            score = vector.cosine(vec, pv)
            if score > best_score:
                second_score = best_score
                best_score = score
                best = p
            elif score > second_score:
                second_score = score
        if best is not None and best_score >= MATCH_THRESHOLD:
            ambiguous = (
                second_score >= MATCH_THRESHOLD
                and (best_score - second_score) < AMBIGUITY_DELTA
            )
            return {
                "id": best["id"],
                "name": best["name"],
                "count": best["count"],
                "status": best["status"],
                "score": round(best_score, 3),
                "match_confidence": round(best_score, 3),
                "match_method": "vector",
                "ambiguous": ambiguous,
            }
        name, category = self._canonicalize_name(problem_text)
        return {
            "id": None,
            "name": name,
            "category": category,
            "count": 0,
            "score": 0.0,
            "match_confidence": 0.0,
            "match_method": "fallback",
            "ambiguous": False,
        }

    def resolve(
        self, business_id: int, problem_text: str, now: str
    ) -> tuple[dict[str, Any], bool]:
        """Return (canonical_problem, created)."""
        vec = vector.embed(problem_text)
        existing = self.repo.list_canonical_problems(business_id)
        best: dict[str, Any] | None = None
        best_vector: list[float] | None = None
        best_score = 0.0
        for p in existing:
            pv = _load_vector(p.get("vector"))
            if pv is None:
                continue
            score = vector.cosine(vec, pv)
            if score > best_score:
                best_score = score
                best = p
                best_vector = pv

        if best is not None and best_score >= MATCH_THRESHOLD:
            updated = self.repo.upsert_canonical_problem(
                business_id,
                best["name"],
                category=best.get("category"),
                vector=best_vector,
                first_seen_at=best["first_seen_at"],
                last_seen_at=now,
                count_delta=1,
            )
            return updated, False

        name, category = self._canonicalize_name(problem_text)
        created = self.repo.upsert_canonical_problem(
            business_id,
            name,
            category=category,
            vector=vec,
            first_seen_at=now,
            last_seen_at=now,
            count_delta=1,
        )
        return created, True
