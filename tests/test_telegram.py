import pytest

from app import secrets
from app.config import Settings
from app.pipeline import Pipeline
from app.telegram_bot import TelegramBot


@pytest.fixture
def bot(settings, repo, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(secrets, "_ENV_PATH", env_file)
    pipeline = Pipeline(repo, settings)
    return TelegramBot(settings, pipeline, repo)


def test_missing_keys_initial(bot):
    assert set(bot._missing_keys()) == {
        "CALLE_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    }
    assert bot._all_complete() is False


def test_apply_secret_updates_settings_and_env(bot, tmp_path, monkeypatch):
    bot._apply_secret("CALLE_API_KEY", "calle_test_value")
    assert bot.settings.calle_api_key == "calle_test_value"
    assert bot.settings.has_calle is True
    # .env file (monkeypatched) contains the value
    content = (tmp_path / ".env").read_text()
    assert "CALLE_API_KEY=calle_test_value" in content


def test_full_setup_flow_reaches_complete(bot):
    bot._apply_secret("CALLE_API_KEY", "k1")
    assert bot._all_complete() is False
    bot._apply_secret("AWS_ACCESS_KEY_ID", "ak")
    assert bot._all_complete() is False
    bot._apply_secret("AWS_SECRET_ACCESS_KEY", "sk")
    assert bot._all_complete() is True
    assert bot._missing_keys() == []
