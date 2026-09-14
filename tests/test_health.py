from app.config import Settings
from app.health import actionable_notices, dependency_status, overall_status
from app.phones import KNOWN_SUPPORTED_REGIONS, region_from_phone


def test_dependency_status_all_missing(tmp_path):
    s = Settings(
        database_path=str(tmp_path / "x.db"),
        aws_access_key_id="", aws_secret_access_key="",
        calle_api_key="", telegram_bot_token="", telegram_admin_chat_id="",
        deepseek_api_key="",
    )
    deps = dependency_status(s, s.database_file)
    assert deps["calle"] == "missing"
    assert deps["aws_bedrock"] == "missing"
    assert deps["telegram"] == "missing"
    assert overall_status(deps) == "degraded"


def test_dependency_status_aws_partial(tmp_path):
    s = Settings(
        database_path=str(tmp_path / "x.db"),
        aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="",
        calle_api_key="", telegram_bot_token="", telegram_admin_chat_id="",
        deepseek_api_key="",
    )
    deps = dependency_status(s, s.database_file)
    assert deps["aws_bedrock"] == "partial"
    assert any("AWS" in n for n in actionable_notices(deps))


def test_dependency_status_configured(tmp_path):
    s = Settings(
        database_path=str(tmp_path / "x.db"),
        aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret",
        calle_api_key="calle_key", telegram_bot_token="t", telegram_admin_chat_id="1",
        deepseek_api_key="",
    )
    deps = dependency_status(s, s.database_file)
    assert deps["calle"] == "configured"
    assert deps["aws_bedrock"] == "configured"
    assert deps["telegram"] == "configured"
    assert overall_status(deps) == "ok"
    assert actionable_notices(deps) == []


def test_notices_never_contain_secrets(tmp_path):
    s = Settings(
        database_path=str(tmp_path / "x.db"),
        aws_access_key_id="AKIA_SUPER_SECRET", aws_secret_access_key="",
        calle_api_key="", telegram_bot_token="", telegram_admin_chat_id="",
        deepseek_api_key="",
    )
    for notice in actionable_notices(dependency_status(s, s.database_file)):
        assert "AKIA_SUPER_SECRET" not in notice


def test_turkey_is_in_known_supported_regions():
    # Turkey IS officially listed by CALL-E (International/testing line).
    assert "TR" in KNOWN_SUPPORTED_REGIONS
    assert region_from_phone("+905445974126") == "TR"
