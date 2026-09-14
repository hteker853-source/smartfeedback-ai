import pytest

from app.config import Settings
from app.db import init_db
from app.repository import Repository


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_path=str(tmp_path / "test.db"),
        aws_access_key_id="",
        aws_secret_access_key="",
        deepseek_api_key="",
        gemini_api_key="",
        calle_api_key="",
        telegram_bot_token="",
        telegram_admin_chat_id="",
        business_name="Test Business",
        business_default_locale="tr",
        supported_call_regions="",
        business_timezone="UTC",
        raw_transcript_retention_days=90,
        api_token="",
        sandbox_action_webhook_url="",
    )


@pytest.fixture
def repo(settings):
    init_db(settings.database_file)
    return Repository(settings.database_file)
