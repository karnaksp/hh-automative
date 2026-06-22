"""LLM provider abstraction for cover letters and questionnaires."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from hh_automative.ai_assist import parse_json_payload
from hh_automative.errors import LLMConfigError, LLMProviderError
from hh_automative.settings import Settings

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMAnswer:
    raw_text: str
    parsed_json: dict[str, Any]
    provider: str
    model: str = ""


class LLMClient(Protocol):
    provider_name: str

    def ask_json(self, prompt: str, timeout_seconds: int) -> LLMAnswer:
        """Return a JSON answer for a prompt."""


class MistralClient:
    provider_name = "mistral"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMConfigError(
                "MISTRAL_API_KEY is required for HH_LLM_PROVIDER=mistral."
            )
        self.api_key = api_key
        self.model = model

    def ask_json(self, prompt: str, timeout_seconds: int) -> LLMAnswer:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON. Do not wrap the response in markdown.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = _post_json(
            "https://api.mistral.ai/v1/chat/completions",
            payload,
            headers,
            timeout_seconds,
            self.provider_name,
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(f"Mistral response has unexpected shape: {data}") from exc
        return _answer_from_text(content, self.provider_name, self.model)


class GeminiClient:
    provider_name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMConfigError(
                "GEMINI_API_KEY is required for HH_LLM_PROVIDER=gemini."
            )
        self.api_key = api_key
        self.model = model

    def ask_json(self, prompt: str, timeout_seconds: int) -> LLMAnswer:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        data = _post_json(url, payload, {}, timeout_seconds, self.provider_name)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            content = "\n".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(f"Gemini response has unexpected shape: {data}") from exc
        return _answer_from_text(content, self.provider_name, self.model)


class OpenRouterClient:
    provider_name = "openrouter"

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMConfigError(
                "OPENROUTER_API_KEY is required for HH_LLM_PROVIDER=openrouter."
            )
        self.api_key = api_key
        self.model = model

    def ask_json(self, prompt: str, timeout_seconds: int) -> LLMAnswer:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON. Do not wrap the response in markdown.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/hh-automative",
            "X-Title": "hh-automative",
        }
        data = _post_json(
            "https://openrouter.ai/api/v1/chat/completions",
            payload,
            headers,
            timeout_seconds,
            self.provider_name,
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"OpenRouter response has unexpected shape: {data}"
            ) from exc
        return _answer_from_text(content, self.provider_name, self.model)


class OllamaClient:
    provider_name = "ollama"

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def ask_json(self, prompt: str, timeout_seconds: int) -> LLMAnswer:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON. Do not wrap the response in markdown.",
                },
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2},
        }
        data = _post_json(
            f"{self.base_url}/api/chat",
            payload,
            {},
            timeout_seconds,
            self.provider_name,
        )
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMProviderError(f"Ollama response has unexpected shape: {data}") from exc
        return _answer_from_text(content, self.provider_name, self.model)


class FallbackLLMClient:
    provider_name = "fallback"

    def __init__(self, clients: list[LLMClient]) -> None:
        if not clients:
            raise LLMConfigError("At least one LLM provider must be configured.")
        self.clients = clients

    def ask_json(self, prompt: str, timeout_seconds: int) -> LLMAnswer:
        errors: list[str] = []
        for client in self.clients:
            try:
                return client.ask_json(prompt, timeout_seconds)
            except Exception as exc:  # noqa: BLE001 - fallback must continue across providers
                provider = getattr(client, "provider_name", client.__class__.__name__)
                LOGGER.warning("LLM provider %s failed: %s", provider, exc)
                errors.append(f"{provider}: {exc}")
        raise LLMProviderError("All LLM providers failed: " + " | ".join(errors))


def create_llm_client(settings: Settings) -> LLMClient | None:
    if not settings.use_llm:
        return None
    provider_names = _provider_chain(settings)
    clients: list[LLMClient] = []
    for provider_name in provider_names:
        if not _provider_has_key(settings, provider_name):
            LOGGER.warning(
                "Skipping LLM provider %s: key/configuration is missing.",
                provider_name,
            )
            continue
        clients.append(_create_provider(settings, provider_name))
    if not clients:
        raise LLMConfigError(
            "LLM is enabled, but no configured providers are available. "
            "Check HH_LLM_PROVIDER, HH_LLM_FALLBACKS, and provider credentials."
        )
    return clients[0] if len(clients) == 1 else FallbackLLMClient(clients)


def llm_config_status(settings: Settings) -> dict[str, Any]:
    provider_names = _provider_chain(settings)
    providers = []
    for provider_name in provider_names:
        providers.append(
            {
                "provider": provider_name,
                "configured": _provider_has_key(settings, provider_name),
                "model": _provider_model(settings, provider_name),
            }
        )
    return {
        "use_llm": settings.use_llm,
        "use_llm_cover_letter": settings.use_llm_cover_letter,
        "use_llm_questionnaire": settings.use_llm_questionnaire,
        "timeout_seconds": settings.llm_timeout_seconds,
        "providers": providers,
    }


def _provider_chain(settings: Settings) -> list[str]:
    result: list[str] = []
    for provider_name in [settings.llm_provider, *settings.llm_fallbacks]:
        if provider_name and provider_name not in result:
            result.append(provider_name)
    return result


def _create_provider(settings: Settings, provider_name: str) -> LLMClient:
    if provider_name == "mistral":
        return MistralClient(settings.mistral_api_key, settings.mistral_model)
    if provider_name == "gemini":
        return GeminiClient(settings.gemini_api_key, settings.gemini_model)
    if provider_name == "openrouter":
        return OpenRouterClient(settings.openrouter_api_key, settings.openrouter_model)
    if provider_name == "ollama":
        return OllamaClient(settings.ollama_base_url, settings.ollama_model)
    raise LLMConfigError(f"Unsupported HH_LLM_PROVIDER: {provider_name}")


def _provider_has_key(settings: Settings, provider_name: str) -> bool:
    if provider_name == "mistral":
        return bool(settings.mistral_api_key)
    if provider_name == "gemini":
        return bool(settings.gemini_api_key)
    if provider_name == "openrouter":
        return bool(settings.openrouter_api_key)
    return provider_name == "ollama"


def _provider_model(settings: Settings, provider_name: str) -> str:
    if provider_name == "mistral":
        return settings.mistral_model
    if provider_name == "gemini":
        return settings.gemini_model
    if provider_name == "openrouter":
        return settings.openrouter_model
    if provider_name == "ollama":
        return settings.ollama_model
    return ""


def _post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: int,
    provider_name: str,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise LLMProviderError(f"{provider_name} request failed: {exc}") from exc


def _answer_from_text(text: str, provider: str, model: str) -> LLMAnswer:
    json_text = _extract_json_object(text)
    try:
        parsed_json = parse_json_payload(json_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMProviderError(f"{provider} response is not valid JSON: {exc}") from exc
    return LLMAnswer(raw_text=text, parsed_json=parsed_json, provider=provider, model=model)


def _extract_json_object(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMProviderError("No JSON object found in LLM response.")
    return text[start : end + 1]
