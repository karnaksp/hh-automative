"""Settings and profile loading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from hh_automative.errors import ConfigError
from hh_automative.models import ResumeProfile, SearchProfile


@dataclass(slots=True)
class Settings:
    username: str
    password: str
    search_profile: str
    default_limit: int
    dry_run: bool
    debug_browser: bool
    headless: bool
    timeout_seconds: int
    chrome_user_data_dir: Path | None
    cookies_path: Path
    local_storage_path: Path
    state_db_path: Path
    analytics_db_path: Path
    log_dir: Path
    diagnostics_dir: Path
    resume_text_path: Path
    use_llm: bool
    use_llm_cover_letter: bool
    use_llm_questionnaire: bool
    llm_provider: str
    llm_fallbacks: list[str]
    llm_timeout_seconds: int
    mistral_api_key: str
    mistral_model: str
    gemini_api_key: str
    gemini_model: str
    openrouter_api_key: str
    openrouter_model: str
    ollama_base_url: str
    ollama_model: str
    resume_text_cache_dir: Path = Path("data/resume-texts")
    login_page: str = "https://hh.ru/account/login"
    search_link: str = "https://hh.ru/"
    search_profiles_path: Path = Path("config/search_profiles.json")
    resumes_path: Path = Path("config/resumes.json")

    @classmethod
    def load(cls) -> Settings:
        load_dotenv()
        return cls(
            username=os.getenv("HH_USERNAME", ""),
            password=os.getenv("HH_PASSWORD", ""),
            search_profile=os.getenv("HH_SEARCH_PROFILE", "data-engineer"),
            default_limit=_int_env("HH_LIMIT", 10),
            dry_run=_bool_env("HH_DRY_RUN", True),
            debug_browser=_bool_env("HH_DEBUG_BROWSER", False),
            headless=_bool_env("HH_HEADLESS", False),
            timeout_seconds=_int_env("HH_TIMEOUT_SECONDS", 10),
            chrome_user_data_dir=_path_env("HH_CHROME_USER_DATA_DIR", Path("browser/chrome-profile")),
            cookies_path=Path(os.getenv("HH_COOKIES_PATH", "auth/cookies.json")),
            local_storage_path=Path(
                os.getenv("HH_LOCAL_STORAGE_PATH", "auth/local_storage.json")
            ),
            state_db_path=Path(os.getenv("HH_STATE_DB_PATH", "data/hh_automative.sqlite3")),
            analytics_db_path=Path(
                os.getenv("HH_ANALYTICS_DB_PATH", "data/hh_automative.duckdb")
            ),
            log_dir=Path(os.getenv("HH_LOG_DIR", "logs")),
            diagnostics_dir=Path(os.getenv("HH_DIAGNOSTICS_DIR", "reports/diagnostics")),
            resume_text_cache_dir=_path_env(
                "HH_RESUME_TEXT_CACHE_DIR", Path("data/resume-texts")
            ),
            resume_text_path=Path(os.getenv("HH_RESUME_TEXT_PATH", "resources/resume-ru.txt")),
            use_llm=_bool_env("HH_USE_LLM", False),
            use_llm_cover_letter=_bool_env("HH_USE_LLM_COVER_LETTER", False),
            use_llm_questionnaire=_bool_env("HH_USE_LLM_QUESTIONNAIRE", False),
            llm_provider=os.getenv("HH_LLM_PROVIDER", "gemini").strip().lower(),
            llm_fallbacks=_csv_env("HH_LLM_FALLBACKS", []),
            llm_timeout_seconds=_int_env("HH_LLM_TIMEOUT_SECONDS", 60),
            mistral_api_key=os.getenv("MISTRAL_API_KEY", ""),
            mistral_model=os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct:free"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
            search_profiles_path=Path(
                os.getenv("HH_SEARCH_PROFILES_PATH", "config/search_profiles.json")
            ),
            resumes_path=Path(os.getenv("HH_RESUMES_PATH", "config/resumes.json")),
        )

    def validate_for_browser(self, require_credentials: bool = True) -> None:
        if self.default_limit < 1:
            raise ConfigError("HH_LIMIT must be greater than zero.")
        if self.timeout_seconds < 1:
            raise ConfigError("HH_TIMEOUT_SECONDS must be greater than zero.")
        if require_credentials and (not self.username or not self.password):
            raise ConfigError("HH_USERNAME and HH_PASSWORD must be set in .env.")

    def ensure_dirs(self) -> None:
        for path in [
            self.cookies_path.parent,
            self.local_storage_path.parent,
            self.state_db_path.parent,
            self.analytics_db_path.parent,
            self.log_dir,
            self.diagnostics_dir,
            self.resume_text_cache_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)
        if self.chrome_user_data_dir is not None:
            self.chrome_user_data_dir.mkdir(parents=True, exist_ok=True)


def load_search_profile(settings: Settings, profile_name: str | None = None) -> SearchProfile:
    data = _read_json(settings.search_profiles_path)
    name = profile_name or settings.search_profile
    try:
        raw_profile = data[name]
    except KeyError as exc:
        available = ", ".join(sorted(data))
        raise ConfigError(f"Search profile '{name}' not found. Available: {available}") from exc
    return SearchProfile(name=name, **raw_profile)


def load_resume_profile(settings: Settings) -> ResumeProfile:
    data = _read_json(settings.resumes_path)
    default_resume = data.get("default_resume")
    resume_codes = {
        resume_name: _normalize_resume_code(resume_code)
        for resume_name, resume_code in data.get("resume_codes", {}).items()
    }
    if not default_resume or default_resume not in resume_codes:
        raise ConfigError("config/resumes.json must define default_resume present in resume_codes.")
    return ResumeProfile(default_resume=default_resume, resume_codes=resume_codes)


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Required config file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc


def _path_env(name: str, default: Path | None = None) -> Path | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return Path(value)


def _normalize_resume_code(raw_code: str) -> str:
    code = (raw_code or "").strip()
    return code.removeprefix("resume_")


def _csv_env(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return [item.strip().lower() for item in value.split(",") if item.strip()]
