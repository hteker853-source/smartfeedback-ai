"""GÖREV 1B: Google Gemini as a third, independent LLM provider.

Mocks the google.generativeai SDK (injected into sys.modules) so these tests
are fully offline/deterministic, matching the rest of the suite. Also checks
that Bedrock/DeepSeek provider-selection behavior is unchanged."""

import sys
import types

import pytest

from app.config import Settings
from app.llm import BedrockProvider, DeepSeekProvider, GeminiProvider, LLMError, get_llm


def _settings(**overrides):
    base = dict(
        database_path="/tmp/x.db", aws_access_key_id="", aws_secret_access_key="",
        deepseek_api_key="", gemini_api_key="", calle_api_key="",
        telegram_bot_token="", telegram_admin_chat_id="",
    )
    base.update(overrides)
    return Settings(**base)


def test_gemini_not_available_without_key():
    s = _settings()
    provider = GeminiProvider(s)
    assert provider.available is False
    with pytest.raises(LLMError):
        provider.chat([{"role": "user", "content": "hi"}])


def _install_fake_genai(monkeypatch, captured):
    fake_genai = types.ModuleType("google.generativeai")

    def configure(api_key):
        captured["api_key"] = api_key

    class _Response:
        text = '{"task": "hello from fake gemini"}'

    class _FakeModel:
        def __init__(self, model_name, system_instruction=None, generation_config=None):
            captured["model_name"] = model_name
            captured["system_instruction"] = system_instruction
            captured["generation_config"] = generation_config

        def generate_content(self, prompt):
            captured["prompt"] = prompt
            return _Response()

    fake_genai.configure = configure
    fake_genai.GenerativeModel = _FakeModel

    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)


def test_gemini_chat_uses_system_instruction_and_json_mode(monkeypatch):
    captured = {}
    _install_fake_genai(monkeypatch, captured)

    s = _settings(gemini_api_key="fake-key", gemini_model="gemini-2.5-flash")
    provider = GeminiProvider(s)
    assert provider.available is True

    result = provider.chat(
        [
            {"role": "system", "content": "You write JSON."},
            {"role": "user", "content": "Say hello."},
        ],
        json_mode=True,
    )
    assert result == '{"task": "hello from fake gemini"}'
    assert captured["model_name"] == "gemini-2.5-flash"
    assert captured["system_instruction"] == "You write JSON."
    assert captured["generation_config"]["response_mime_type"] == "application/json"
    assert "Say hello." in captured["prompt"]
    assert captured["api_key"] == "fake-key"


def test_get_llm_prefers_gemini_when_configured_and_selected():
    s = _settings(gemini_api_key="fake-key", llm_provider="gemini")
    assert isinstance(get_llm(s), GeminiProvider)


def test_get_llm_falls_back_to_deepseek_when_gemini_selected_but_not_configured():
    s = _settings(deepseek_api_key="fake-key", llm_provider="gemini")  # no gemini key
    assert isinstance(get_llm(s), DeepSeekProvider)


def test_get_llm_bedrock_and_deepseek_behavior_unchanged():
    # Existing behavior must be untouched by adding Gemini.
    s = _settings(deepseek_api_key="fake-key")
    assert isinstance(get_llm(s), DeepSeekProvider)

    s2 = _settings(aws_access_key_id="a", aws_secret_access_key="b", llm_provider="bedrock")
    assert isinstance(get_llm(s2), BedrockProvider)

    s3 = _settings()  # nothing configured
    assert get_llm(s3) is None


def test_make_model_selects_gemini_and_records_provenance():
    from app.agents.strands_agents import _make_model

    s = _settings(gemini_api_key="fake-key", gemini_model="gemini-2.5-flash", llm_provider="gemini")
    model, provenance = _make_model(s)
    assert provenance.provider == "gemini"
    assert provenance.model == "gemini-2.5-flash"
    assert provenance.fallback_occurred is False


def test_make_model_falls_back_to_deepseek_if_gemini_build_fails(monkeypatch):
    from app.agents import strands_agents

    def _boom(settings):
        raise RuntimeError("boom")

    monkeypatch.setattr(strands_agents, "_make_gemini_model", _boom)
    s = _settings(gemini_api_key="fake-key", deepseek_api_key="fake-ds-key", llm_provider="gemini")
    model, provenance = strands_agents._make_model(s)
    assert provenance.provider == "deepseek"
    assert provenance.fallback_occurred is True


def test_make_model_bedrock_and_default_paths_unchanged():
    from app.agents.strands_agents import _make_model

    s = _settings(deepseek_api_key="fake-key")  # default provider, no gemini/bedrock
    _model, provenance = _make_model(s)
    assert provenance.provider == "deepseek"
    assert provenance.fallback_occurred is False
