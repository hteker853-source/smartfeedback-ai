"""Safe .env secret management for the Telegram setup flow.

Writes secrets to `.env` without ever echoing them back to the user or to logs.
"""

from __future__ import annotations

from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def env_path() -> Path:
    return _ENV_PATH


def _read_lines() -> list[str]:
    if not _ENV_PATH.exists():
        return []
    return _ENV_PATH.read_text(encoding="utf-8").splitlines()


def set_env_var(key: str, value: str) -> None:
    value = value.strip()
    lines = _read_lines()
    new_lines: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def get_env_var(key: str) -> str:
    for line in _read_lines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return ""


def redact(value: str) -> str:
    if len(value) <= 6:
        return "****"
    return value[:3] + "****" + value[-3:]
