"""Logging setup with secret redaction.

Ensures secrets (Telegram token, CALL-E key, AWS keys, DeepSeek key) never
appear in log output, including in exception tracebacks.
"""

from __future__ import annotations

import logging

from .config import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: list[str], fmt: str | None = None, datefmt: str | None = None) -> None:
        super().__init__(fmt, datefmt)
        self._secrets = [s for s in secrets if s]

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "***")
        return rendered


def setup_logging(settings: Settings) -> None:
    secrets = [
        settings.telegram_bot_token,
        settings.calle_api_key,
        settings.aws_access_key_id,
        settings.aws_secret_access_key,
        settings.deepseek_api_key,
    ]
    formatter = RedactingFormatter(
        secrets,
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        from logging.handlers import RotatingFileHandler

        handlers.append(
            RotatingFileHandler(
                settings.log_file, maxBytes=5 * 1024 * 1024, backupCount=5,
                encoding="utf-8",
            )
        )
    for h in handlers:
        h.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
