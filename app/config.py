"""Application configuration loaded from environment variables.

Secrets are only read from the environment / `.env` file and are never
written to logs, committed, or sent back to Telegram.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    # Telegram
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    # CALL-E
    calle_api_key: str = ""
    calle_base_url: str = "https://api.heycall-e.com"
    calle_webhook_base_url: str = ""

    # AWS Bedrock
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-west-2"
    bedrock_model_id: str = "us.amazon.nova-lite-v1:0"

    # DeepSeek (OpenAI-compatible model provider)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Google Gemini — added as a third, independent model provider (an
    # emergency/standby option) while Bedrock (AWS quota/access pending) and
    # DeepSeek (billing) are both temporarily unavailable. Bedrock and
    # DeepSeek code paths are left fully intact: the architecture is
    # provider-agnostic by design, and either can become primary again the
    # moment its account issue is resolved.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Which LLM provider drives the agent layer: "bedrock", "deepseek", or
    # "gemini". Bedrock/Gemini each fall back to DeepSeek on build failure
    # (see app/agents/strands_agents.py::_make_model).
    llm_provider: str = "gemini"

    # Application
    database_path: str = "data/smartfeedback.db"
    business_name: str = "SmartFeedback Demo"
    business_default_locale: str = "tr"
    # Gelecekte farklı BUSINESS_TYPE değerleri (örn. "appointment_reminder",
    # "subscription_renewal") farklı tetikleme mantığı kullanabilir; şu an
    # sadece "order_fulfillment" uygulanmıştır. Bu alan henüz hiçbir kod
    # tarafından okunmuyor / dallanma yapmıyor — sadece genişletilebilirlik
    # niyetini gösteren bir yer tutucu.
    business_type: str = "order_fulfillment"
    post_delivery_delay_minutes: int = 30
    business_close_hour: int = 2
    morning_summary_hour: int = 8
    business_timezone: str = "UTC"
    # Raw transcript retention in days (0 = keep forever). Structured data is kept.
    raw_transcript_retention_days: int = 90
    # Comma-separated ISO-3166 regions this deployment may call. Empty = rely on
    # CALL-E's own region/language validation (recommended, since CALL-E applies
    # temporary regional restrictions we cannot know locally).
    supported_call_regions: str = ""
    # Deployment environment tag carried in CALL-E call metadata.
    environment: str = "dev"
    # Optional shared token guarding mutating API endpoints (empty = disabled).
    api_token: str = ""
    # Deterministic policy gate thresholds.
    # Minimum aggregated confidence required for an autonomous alert.
    min_alert_confidence: float = 0.6
    # Number of occurrences for a canonical problem to be treated as recurring.
    recurring_threshold: int = 3
    # Pre-call contextualization: only verified, structured case history above
    # this confidence may steer the next call's opening (never raw transcripts).
    context_min_confidence: float = 0.6
    context_max_items: int = 2
    # GÖREV F: a customer's time-since-last-order must reach this multiple of
    # their OWN historical average ordering interval before being flagged as
    # a "frequency drop" (confidence-of-loss) risk. E.g. 2.0 = "twice as long
    # as their normal rhythm". Not a fixed day count — scales per customer.
    frequency_drop_ratio_threshold: float = 2.0
    # Minimum number of past orders required to compute a customer's "normal
    # rhythm" at all; below this, the signal is insufficient_data rather than
    # a guess (needs at least 3 intervals between orders).
    frequency_drop_min_orders: int = 4
    # Downstream action executor. Fires ONLY after human approval, to a sandbox
    # webhook receiver. Empty (default) = OFF. Never enabled in production by
    # accident; it is a demo/sandbox side-effect channel.
    sandbox_action_webhook_url: str = ""
    sandbox_action_timeout_seconds: float = 5.0
    # Database backup directory (empty = no automatic backups).
    backup_dir: str = "backups"
    # Number of backup files to keep.
    backup_keep: int = 7
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    # Optional rotating log file (empty = stdout only, e.g. for systemd journald).
    log_file: str = ""

    @property
    def database_file(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return path

    @property
    def supported_regions(self) -> set[str]:
        return {
            r.strip().upper()
            for r in (self.supported_call_regions or "").split(",")
            if r.strip()
        }

    @property
    def has_aws(self) -> bool:
        return bool(self.aws_access_key_id and self.aws_secret_access_key)

    @property
    def has_calle(self) -> bool:
        return bool(self.calle_api_key)

    @property
    def has_deepseek(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def llm_enabled(self) -> bool:
        return self.has_aws or self.has_deepseek or self.has_gemini


@lru_cache
def get_settings() -> Settings:
    return Settings()
