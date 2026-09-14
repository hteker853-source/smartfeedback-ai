"""Dependency-level health status.

Presence-based (does not perform live network calls, to keep /health cheap).
Validity of a provider key is verified lazily by the provider, never here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def database_status(db_path: Path) -> str:
    try:
        from .db import connect

        with connect(db_path) as conn:
            conn.execute("SELECT 1").fetchone()
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


def dependency_status(settings: Any, db_path: Path) -> dict[str, str]:
    aws = "configured" if settings.has_aws else (
        "partial" if (settings.aws_access_key_id or settings.aws_secret_access_key) else "missing"
    )
    telegram = (
        "configured"
        if settings.telegram_bot_token and settings.telegram_admin_chat_id
        else "partial" if settings.telegram_bot_token else "missing"
    )
    return {
        "application": "ok",
        "database": database_status(db_path),
        "calle": "configured" if settings.has_calle else "missing",
        "aws_bedrock": aws,
        "telegram": telegram,
        "deepseek": "configured" if settings.has_deepseek else "missing",
    }


def overall_status(deps: dict[str, str]) -> str:
    if deps.get("database") != "ok":
        return "degraded"
    if deps.get("calle") == "missing":
        return "degraded"
    return "ok"


def actionable_notices(deps: dict[str, str]) -> list[str]:
    """Short, actionable messages for missing/partial dependencies (no secrets)."""
    notices: list[str] = []
    if deps.get("database") == "error":
        notices.append("⚠️ SmartFeedback veritabanına erişilemiyor. Veritabanı yolunu kontrol edin.")
    if deps.get("calle") == "missing":
        notices.append("⚠️ CALL-E API anahtarı eksik. /setup ile girin veya .env dosyasına ekleyin.")
    if deps.get("aws_bedrock") in ("missing", "partial"):
        notices.append(
            "⚠️ AWS/Bedrock kimlik bilgileri eksik. Bedrock analizleri şu anda kullanılamıyor; "
            "deterministik fallback devrede. /setup ile girin veya .env dosyasına ekleyin."
        )
    if deps.get("telegram") in ("missing", "partial"):
        notices.append("⚠️ Telegram yapılandırması eksik. Bildirimler gönderilemez.")
    return notices
