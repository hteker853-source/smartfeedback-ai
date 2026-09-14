"""Deterministic feedback analyzer (fallback when no LLM is configured).

Keeps the pipeline runnable and testable offline. Real deployments use the
Strands Feedback agent; this module mirrors the same structured output.
"""

from __future__ import annotations

import re

from .schemas import Feedback, MissingProduct, Problem

_POSITIVE = {
    "tr": ["güzel", "harika", "memnun", "çok iyi", "beğendim", "lezzetli", "süper", "teşekkür", "mükemmel", "iyi"],
    "en": ["great", "good", "loved", "delicious", "happy", "satisfied", "excellent", "perfect", "thank", "nice"],
    "de": ["gut", "super", "lecker", "zufrieden", "danke", "perfekt", "prima", "ausgezeichnet"],
}
_NEGATIVE = {
    "tr": ["soğuk", "kötü", "değildi", "değil", "geç", "bekledim", "eksik", "gelmedi", "yoktu", "bozuk", "yanlış", "memnun", "problem", "sorun", "şikayet", "pişman"],
    "en": ["cold", "bad", "late", "missing", "didn't", "did not", "never", "wrong", "broken", "disappointed", "complaint", "terrible", "poor"],
    "de": ["kalt", "schlecht", "spät", "fehlt", "nicht", "nie", "falsch", "kaputt", "enttäuscht", "beschwerde"],
}

# Substantive negative words that become Problem entries (negation markers and
# missing-verb words are used for sentiment/missing detection, not as problems).
_PROBLEM_WORDS = {
    "tr": ["soğuk", "kötü", "geç", "bekledim", "bozuk", "yanlış", "problem", "sorun", "şikayet", "pişman"],
    "en": ["cold", "bad", "late", "wrong", "broken", "disappointed", "complaint", "terrible", "poor"],
    "de": ["kalt", "schlecht", "spät", "falsch", "kaputt", "enttäuscht", "beschwerde"],
}
_FILLERS = {
    "ama", "fakat", "ise", "ve", "o", "da", "de", "bir", "ben", "biz", "bize",
    "the", "a", "an", "but", "and", "it", "was", "were", "my", "they",
}

# Patterns capturing a product noun right before an ordering verb.
_PRODUCT_BEFORE_ORDER = {
    "tr": r"([a-zçğıöşüâîû]{2,20})\s+(?:sipariş|istemiş|istedi|almış|söylemiş|rica)",
    "en": r"([a-z]{2,20})\s+(?:order|ordered|asked for|wanted|requested)",
    "de": r"([a-zäöüß]{2,20})\s+(?:bestellt|bestellte|gewünscht|verlangt)",
}

# Missing verbs; the product noun is the nearest non-filler word before them.
_MISSING_VERBS = {
    "tr": ["gelmedi", "yoktu", "eksikti", "eksik", "unutulmuş", "gönderilmemiş", "göndermemiş"],
    "en": ["missing", "didn't come", "did not come", "never arrived", "wasn't there", "was not there"],
    "de": ["fehlt", "gefehlt", "nicht gekommen", "nicht dabei", "vergessen"],
}

# Negation that *cancels* a missing/absence signal when it directly qualifies
# the marker: Turkish negates after the verb ("eksik değildi" = "was not
# missing"); English/German negate immediately before ("not missing").
_NEGATION_FOLLOW = {
    "tr": ("değil", "değildi", "değildir", "değildik"),
}
_NEGATION_PRECEDE = (
    "not ", "wasn't ", "isn't ", "weren't ", "aren't ", "was not ", "is not ",
    "were not ", "nicht ", "kein ", "keine ",
)


def _negated(text: str, verb: str, idx: int, locale: str) -> bool:
    after = text[idx + len(verb): idx + len(verb) + 16]
    before = text[max(0, idx - 14): idx]
    if locale == "tr":
        return any(w in after for w in _NEGATION_FOLLOW.get("tr", ()))
    return any(w in before for w in _NEGATION_PRECEDE)


def has_missing_signal(transcript: str, locale: str) -> bool:
    """True if the transcript contains a non-negated missing/absence signal.

    "ürün eksik değildi" / "the item was not missing" must NOT count as a
    missing signal; "ayran gelmedi" / "the burger was missing" must.
    """
    text = transcript.lower()
    locale = locale if locale in _MISSING_VERBS else "en"
    for marker in _MISSING_VERBS[locale]:
        idx = text.find(marker)
        while idx != -1:
            if not _negated(text, marker, idx, locale):
                return True
            idx = text.find(marker, idx + 1)
    return False


def has_negated_missing_signal(transcript: str, locale: str) -> bool:
    """True if a missing/absence signal is present but explicitly negated
    (e.g. "ürün eksik değildi"). Used by the verifier to mark CONTRADICTED."""
    text = transcript.lower()
    locale = locale if locale in _MISSING_VERBS else "en"
    for marker in _MISSING_VERBS[locale]:
        idx = text.find(marker)
        while idx != -1:
            if _negated(text, marker, idx, locale):
                return True
            idx = text.find(marker, idx + 1)
    return False

# "Do not call" request phrases. Kept as multi-word phrases to avoid false
# positives (a single "call"/"arama" token is too broad).
_DNC_PHRASES = {
    "tr": [
        "beni arama",
        "beni aramayın",
        "bir daha arama",
        "bir daha aramayın",
        "tekrar arama",
        "tekrar aramayın",
        "beni bir daha arama",
        "aramayın beni",
        "rahatsız etmeyin",
    ],
    "en": [
        "don't call",
        "dont call",
        "do not call",
        "stop calling",
        "never call me",
        "remove me from",
    ],
    "de": [
        "nicht anrufen",
        "nicht mehr anrufen",
        "rufen sie nicht",
        "nicht wieder anrufen",
    ],
}


def detect_dnc(transcript: str, locale: str = "tr") -> bool:
    """True if the customer explicitly asked not to be called again.

    The transcript is treated as untrusted customer content; this only detects a
    narrow, explicit request and never parses any instruction from it.
    """
    text = transcript.lower()
    phrases = _DNC_PHRASES.get(locale, _DNC_PHRASES["tr"])
    return any(p in text for p in phrases)


def _nearest_noun(before_tokens: list[str]) -> str | None:
    for token in reversed(before_tokens):
        token = token.strip().lower()
        if len(token) > 2 and token.isalpha() and token not in _FILLERS:
            return token
    return None


def detect_missing_products(transcript: str, locale: str) -> list[MissingProduct]:
    text = transcript.lower()
    locale = locale if locale in _MISSING_VERBS else "en"
    items: list[str] = []

    ordered = _PRODUCT_BEFORE_ORDER.get(locale)
    if ordered:
        m = re.search(ordered, text)
        if m and m.group(1) not in _FILLERS:
            items.append(m.group(1))

    verbs = _MISSING_VERBS[locale]
    has_verb = has_missing_signal(transcript, locale)

    # Fall back to nearest-noun scanning only when no explicit "X ordered" match.
    if not items:
        joined = "|".join(re.escape(v) for v in verbs)
        for m in re.finditer(joined, text):
            verb = m.group(0)
            if _negated(text, verb, m.start(), locale):
                continue  # "eksik değildi" / "not missing" is not a missing item
            before = re.split(r"\s+", transcript[: m.start()].strip().lower())
            noun = _nearest_noun(before)
            if noun:
                items.append(noun)

    if not has_verb and not items:
        return []

    seen: list[str] = []
    for it in items:
        if it not in seen:
            seen.append(it)
    if not seen:
        return [MissingProduct(name="item", urgency="high", notify=True)]
    return [MissingProduct(name=it, urgency="high", notify=True) for it in seen[:3]]


def analyze_feedback(transcript: str, locale: str = "tr") -> Feedback:
    text = transcript.lower()
    locale = locale if locale in ("tr", "en", "de") else "tr"
    pos = _POSITIVE[locale]
    neg = _NEGATIVE[locale]
    pos_count = sum(1 for w in pos if w in text)
    neg_count = sum(1 for w in neg if w in text)

    if pos_count > neg_count:
        sentiment = "positive"
    elif neg_count > pos_count:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    missing = detect_missing_products(transcript, locale)
    problems: list[Problem] = []
    positives: list[str] = []

    for w in pos:
        if w in text and w not in positives:
            positives.append(w)
    for w in _PROBLEM_WORDS[locale]:
        if w in text:
            problems.append(Problem(text=w, category="general", severity="medium"))

    if missing:
        urgency = "critical"
        priority = "critical"
    elif neg_count >= 2:
        urgency = "high"
        priority = "high"
    elif neg_count == 1:
        urgency = "medium"
        priority = "medium"
    else:
        urgency = "low"
        priority = "low"

    satisfaction = None
    if sentiment == "positive":
        satisfaction = 5 if pos_count >= 3 else 4
    elif sentiment == "negative":
        satisfaction = 1 if neg_count >= 3 else 2
    else:
        satisfaction = 3

    action = ""
    if missing:
        names = ", ".join(m.name for m in missing)
        action = f"Check whether {names} was missing from the order and contact the customer."
    elif sentiment == "negative":
        action = "Review the reported issue and consider a follow-up with the customer."

    summary = transcript.strip()[:400]

    return Feedback(
        satisfaction=satisfaction,
        sentiment=sentiment,
        positives=positives,
        problems=problems,
        category="general",
        priority=priority,
        urgency=urgency,
        missing_products=missing,
        recommended_action=action,
        language=locale,
        summary=summary,
    )
