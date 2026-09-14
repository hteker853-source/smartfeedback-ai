"""Phone number validation and region support.

CALL-E publishes a supported-regions table (docs.heycall-e.com/regions). We
validate numbers to E.164 and optionally gate on a configured region list.
Because CALL-E also applies *temporary* regional restrictions (cyberattack risk
controls) that we cannot know locally, the provider remains the authoritative
source for region/language rejection; we map its error codes to clear messages.
"""

from __future__ import annotations

import re

# Official CALL-E supported regions (ISO-3166 -> calling code prefix).
# Source: https://docs.heycall-e.com/regions
_REGION_BY_PREFIX = {
    "+1": "US",
    "+65": "SG",
    "+60": "MY",
    "+91": "IN",
    "+971": "AE",
    "+61": "AU",
    "+44": "GB",
    "+84": "VN",
    "+49": "DE",
    "+81": "JP",
    "+33": "FR",
    "+52": "MX",
    "+55": "BR",
    "+62": "ID",
    "+63": "PH",
    "+254": "KE",
    "+31": "NL",
    "+48": "PL",
    "+880": "BD",
    "+234": "NG",
    "+968": "OM",
    "+66": "TH",
    "+264": "NA",
    "+237": "CM",
    "+258": "MZ",
    "+966": "SA",
    "+358": "FI",
    "+380": "UA",
    "+94": "LK",
    "+267": "BW",
    "+92": "PK",
    "+90": "TR",
    "+504": "HN",
    "+34": "ES",
    "+886": "TW",
    "+27": "ZA",
    "+20": "EG",
    "+233": "GH",
    "+972": "IL",
    "+353": "IE",
    "+216": "TN",
}

# Officially supported regions (for documentation / health).
KNOWN_SUPPORTED_REGIONS = frozenset(_REGION_BY_PREFIX.values())

# Local subscriber length bounds for a few regions we validate strictly.
_COUNTRY_CODE_LENGTHS = {
    "TR": (10, 10),
    "US": (10, 10),
    "GB": (10, 10),
    "DE": (10, 11),
    "NL": (9, 9),
    "FR": (9, 9),
    "ES": (9, 9),
    "IT": (9, 10),
    "AU": (9, 9),
    "NZ": (8, 9),
    "JP": (10, 10),
}

_PHONE_CHARS = re.compile(r"[^\d+]")


def strip(phone: str) -> str:
    return _PHONE_CHARS.sub("", (phone or "").strip())


def normalize_phone(phone: str) -> str:
    """Normalize to E.164. Bare 10-digit numbers are assumed to be Turkish."""
    p = strip(phone)
    if p.startswith("+"):
        return p
    if p.startswith("00"):
        return "+" + p[2:]
    if p.startswith("0"):
        return "+90" + p[1:]
    if len(p) == 10:
        return "+90" + p
    return "+" + p


def region_from_phone(phone: str) -> str | None:
    # Longest-prefix match (e.g. +971 before +97).
    for prefix in sorted(_REGION_BY_PREFIX, key=len, reverse=True):
        if phone.startswith(prefix):
            return _REGION_BY_PREFIX[prefix]
    return None


def country_code_for(region: str) -> str:
    for prefix, r in _REGION_BY_PREFIX.items():
        if r == region:
            return prefix[1:]
    return ""


def validate_phone(phone: str, supported_regions: set[str]) -> tuple[bool, str, str]:
    """Validate and normalize a phone number.

    Returns (ok, normalized_phone, error_message).
    """
    p = strip(phone)
    if not p:
        return False, "", "Telefon numarası boş."

    if p.startswith("+"):
        digits = p[1:]
    elif p.startswith("00"):
        digits = p[2:]
    elif p.startswith("0"):
        digits = "90" + p[1:]
    else:
        digits = p

    if not digits.isdigit():
        return False, "", "Geçersiz telefon numarası formatı."
    if len(digits) < 7 or len(digits) > 15:
        return False, "", "Telefon numarası uzunluğu geçersiz (E.164)."

    normalized = normalize_phone(phone)
    region = region_from_phone(normalized)
    if region is None:
        return False, "", "Numaranın ülke kodu tanınamadı."

    if supported_regions and region not in supported_regions:
        return (
            False,
            "",
            f"{region} bölgesi bu kurulumda desteklenmiyor "
            f"(desteklenen bölgeler: {', '.join(sorted(supported_regions))}).",
        )

    if region in _COUNTRY_CODE_LENGTHS:
        lo, hi = _COUNTRY_CODE_LENGTHS[region]
        local_len = len(digits) - len(country_code_for(region))
        if not (lo <= local_len <= hi):
            return False, "", f"{region} için numara uzunluğu geçersiz."

    return True, normalized, ""


# Human-readable mapping of CALL-E stable error codes to actionable messages.
CALLE_ERROR_MESSAGES = {
    "unsupported_region": (
        "Bu ülke/bölge CALL-E tarafından şu anda desteklenmiyor "
        "(geçici bölgesel kısıtlama olabilir)."
    ),
    "unsupported_language": (
        "Bu dil/bölge kombinasyonu CALL-E tarafından desteklenmiyor."
    ),
    "insufficient_balance": "CALL-E hesap bakiyesi yetersiz.",
    "rate_limit_exceeded": "CALL-E hız limiti aşıldı; bir süre sonra tekrar deneyin.",
    "invalid_phone": "Telefon numarası geçersiz E.164 formatında değil.",
    "unauthorized": "CALL-E API anahtarı geçersiz.",
}


def calle_error_message(code: str, default: str = "") -> str:
    return CALLE_ERROR_MESSAGES.get(code, default)
