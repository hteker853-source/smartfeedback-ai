"""Lightweight deterministic text embeddings stored in SQLite.

Used for canonical problem matching. No external vector DB (Pinecone) is
used; vectors are small JSON float lists persisted in the `canonical_problems`
table.
"""

from __future__ import annotations

import math
import re
import unicodedata

DIM = 256
_STOPWORDS = {
    "the", "a", "an", "and", "or", "is", "was", "wasn", "t", "not", "i", "my",
    "de", "en", "tr", "bir", "ve", "veya", "ile", "çok", "ama", "için", "geldi",
    "die", "der", "das", "und", "oder", "nicht", "zu", "im", "in", "ein", "eine",
}

# Deterministic negation handling so "not hot" maps to "cold" etc. This is a
# fallback heuristic; the LLM (when available) does richer canonicalization.
_NEGATION_MAP = [
    ("scak degildi", "soguk"),
    ("scak degil", "soguk"),
    ("soguk degildi", "scak"),
    ("soguk degil", "scak"),
    ("not hot", "cold"),
    ("wasnt hot", "cold"),
    ("was not hot", "cold"),
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for src, dst in _NEGATION_MAP:
        text = text.replace(src, dst)
    tokens = [t for t in text.split() if t and t not in _STOPWORDS]
    return " ".join(tokens)


def _hash_ngram(ngram: str) -> int:
    h = 2166136261
    for ch in ngram:
        h = (h ^ ord(ch)) * 16777619
        h &= 0xFFFFFFFF
    return h % DIM


def embed(text: str, dim: int = DIM) -> list[float]:
    """Character 3-gram hashing embedding, L2-normalized."""
    norm = normalize(text)
    if not norm:
        return [0.0] * dim
    vec = [0.0] * dim
    padded = " " + norm + " "
    for i in range(len(padded) - 2):
        idx = _hash_ngram(padded[i : i + 3])
        vec[idx] += 1.0
    norm_val = math.sqrt(sum(v * v for v in vec))
    if norm_val == 0:
        return vec
    return [v / norm_val for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))
