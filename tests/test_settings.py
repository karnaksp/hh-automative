from __future__ import annotations

from hh_automative.settings import Settings, load_resume_profile, load_search_profile


def test_load_default_profiles() -> None:
    settings = Settings.load()

    search_profile = load_search_profile(settings, settings.search_profile)
    resume_profile = load_resume_profile(settings)

    assert search_profile.query
    assert resume_profile.default_resume in resume_profile.resume_codes


def test_check_config_does_not_require_credentials() -> None:
    settings = Settings.load()

    settings.validate_for_browser(require_credentials=False)


def test_settings_default_search_profile_is_data_engineer(monkeypatch) -> None:
    monkeypatch.delenv("HH_SEARCH_PROFILE", raising=False)

    settings = Settings.load()

    assert settings.search_profile == "data-engineer"


def test_settings_supports_runtime_config_paths(monkeypatch, tmp_path) -> None:
    search_profiles_path = tmp_path / "search.json"
    resumes_path = tmp_path / "resumes.json"
    monkeypatch.setenv("HH_SEARCH_PROFILES_PATH", str(search_profiles_path))
    monkeypatch.setenv("HH_RESUMES_PATH", str(resumes_path))

    settings = Settings.load()

    assert settings.search_profiles_path == search_profiles_path
    assert settings.resumes_path == resumes_path
