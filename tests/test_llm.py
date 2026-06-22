from __future__ import annotations

from pathlib import Path

from hh_automative.errors import LLMProviderError
from hh_automative.llm import (
    FallbackLLMClient,
    LLMAnswer,
    OllamaClient,
    _answer_from_text,
    create_llm_client,
    llm_config_status,
)
from hh_automative.settings import Settings


class FailingClient:
    provider_name = "failing"

    def ask_json(self, _prompt: str, _timeout_seconds: int) -> LLMAnswer:
        raise LLMProviderError("temporary failure")


class WorkingClient:
    provider_name = "working"

    def ask_json(self, _prompt: str, _timeout_seconds: int) -> LLMAnswer:
        return LLMAnswer(raw_text='{"ok": true}', parsed_json={"ok": True}, provider="working")


def test_answer_from_text_extracts_json() -> None:
    answer = _answer_from_text('Ответ: {"cover_letter": "hello"}', "provider", "model")

    assert answer.parsed_json == {"cover_letter": "hello"}
    assert answer.provider == "provider"


def test_fallback_client_uses_next_provider() -> None:
    client = FallbackLLMClient([FailingClient(), WorkingClient()])

    answer = client.ask_json("prompt", 5)

    assert answer.parsed_json == {"ok": True}
    assert answer.provider == "working"


def test_ollama_provider_does_not_require_api_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path, llm_provider="ollama")

    client = create_llm_client(settings)

    assert isinstance(client, OllamaClient)
    assert client.model == "llama3.1:8b"


def test_llm_config_status_marks_ollama_configured(tmp_path: Path) -> None:
    settings = _settings(tmp_path, llm_provider="ollama")

    status = llm_config_status(settings)

    assert status["providers"] == [
        {"provider": "ollama", "configured": True, "model": "llama3.1:8b"}
    ]


def test_llm_config_status_deduplicates_provider_chain(tmp_path: Path) -> None:
    settings = _settings(tmp_path, llm_provider="ollama")
    settings.llm_fallbacks = ["mistral", "ollama"]

    status = llm_config_status(settings)

    assert [provider["provider"] for provider in status["providers"]] == ["ollama", "mistral"]


def test_create_llm_client_skips_unconfigured_fallback_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path, llm_provider="gemini")
    settings.llm_fallbacks = ["openrouter", "mistral"]
    settings.mistral_api_key = "token"

    client = create_llm_client(settings)

    assert client is not None


def _settings(tmp_path: Path, llm_provider: str = "gemini") -> Settings:
    return Settings(
        username="",
        password="",
        search_profile="data-engineer",
        default_limit=1,
        dry_run=True,
        debug_browser=False,
        headless=False,
        timeout_seconds=1,
        chrome_user_data_dir=None,
        cookies_path=tmp_path / "auth" / "cookies.json",
        local_storage_path=tmp_path / "auth" / "local_storage.json",
        state_db_path=tmp_path / "state.sqlite3",
        analytics_db_path=tmp_path / "analytics.duckdb",
        log_dir=tmp_path / "logs",
        diagnostics_dir=tmp_path / "diagnostics",
        resume_text_path=tmp_path / "resume.txt",
        use_llm=True,
        use_llm_cover_letter=True,
        use_llm_questionnaire=True,
        llm_provider=llm_provider,
        llm_fallbacks=[],
        llm_timeout_seconds=5,
        mistral_api_key="",
        mistral_model="mistral-small-latest",
        gemini_api_key="key",
        gemini_model="gemini-1.5-flash",
        openrouter_api_key="",
        openrouter_model="mistralai/mistral-7b-instruct:free",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.1:8b",
    )
