"""LLM providers.

- Amazon Bedrock (primary, used with Strands Agents SDK for the agent layer).
- DeepSeek (optional OpenAI-compatible endpoint, used for canonical problem
  matching / natural-language query when available).

Secrets come only from settings; nothing is logged.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings


class LLMError(RuntimeError):
    pass


class BedrockProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.settings.aws_region,
                aws_access_key_id=self.settings.aws_access_key_id,
                aws_secret_access_key=self.settings.aws_secret_access_key,
            )
        return self._client

    @property
    def available(self) -> bool:
        return self.settings.has_aws

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        if not self.available:
            raise LLMError("Bedrock not configured")
        client = self._get_client()
        response = client.converse(
            modelId=self.settings.bedrock_model_id,
            messages=messages,
        )
        output = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(
            part.get("text", "")
            for part in output
            if isinstance(part, dict) and "text" in part
        )
        if json_mode:
            text = _extract_json_object(text)
        return text


class GeminiProvider:
    """Google Gemini — a third, independent model provider (see
    Settings.gemini_api_key). Added as a standby option while Bedrock (AWS
    access/quota pending) and DeepSeek (billing) are both temporarily
    unavailable; both of those providers are left fully intact and can
    become primary again the moment their account issue is resolved.

    Uses the `google-generativeai` SDK directly (same "simple .chat()"
    pattern as BedrockProvider/DeepSeekProvider), independent of the
    Strands-native Gemini adapter used by the agent layer
    (app/agents/strands_agents.py) — this class only serves complete_json()
    callers (canonical-problem naming, natural-language query, CALL-E
    call-script generation).
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return self.settings.has_gemini

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        if not self.available:
            raise LLMError("Gemini not configured")
        import google.generativeai as genai

        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        user_parts = [m["content"] for m in messages if m.get("role") != "system"]

        genai.configure(api_key=self.settings.gemini_api_key)
        generation_config: dict[str, Any] = {"temperature": 0.0}
        if json_mode:
            generation_config["response_mime_type"] = "application/json"
        model = genai.GenerativeModel(
            self.settings.gemini_model,
            system_instruction="\n".join(system_parts) or None,
            generation_config=generation_config,
        )
        response = model.generate_content("\n\n".join(user_parts))
        text = response.text
        if json_mode:
            text = _extract_json_object(text)
        return text


class DeepSeekProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return self.settings.has_deepseek

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        if not self.available:
            raise LLMError("DeepSeek not configured")
        url = self.settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.settings.deepseek_api_key}"}
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if json_mode:
            content = _extract_json_object(content)
        return content


def _extract_json_object(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def get_llm(
    settings: Settings,
) -> "GeminiProvider | BedrockProvider | DeepSeekProvider | None":
    """Select a provider for complete_json() callers (canonical-problem
    naming, natural-language query, CALL-E call-script generation).

    Prefers `settings.llm_provider` when it names a provider that is actually
    configured; otherwise falls back through the others (DeepSeek, then
    Bedrock, then Gemini) so a misconfigured/unavailable preferred provider
    never blocks these non-critical callers outright. This mirrors the same
    "prefer configured choice, degrade gracefully" philosophy as the Strands
    agent layer's `_make_model` (app/agents/strands_agents.py).
    """
    provider = (settings.llm_provider or "").strip().lower()
    if provider == "gemini" and settings.has_gemini:
        return GeminiProvider(settings)
    if provider == "bedrock" and settings.has_aws:
        return BedrockProvider(settings)
    # DeepSeek was historically the default model brain; Bedrock/Gemini are
    # last-resort fallbacks here when no provider preference matched above.
    if settings.has_deepseek:
        return DeepSeekProvider(settings)
    if settings.has_aws:
        return BedrockProvider(settings)
    if settings.has_gemini:
        return GeminiProvider(settings)
    return None


def complete_json(llm: Any, system: str, user: str) -> dict[str, Any] | None:
    """Ask the LLM for a JSON object; returns None on failure."""
    if llm is None:
        return None
    try:
        text = llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        return json.loads(text)
    except (json.JSONDecodeError, httpx.HTTPError, LLMError, Exception):  # noqa: BLE001
        return None
