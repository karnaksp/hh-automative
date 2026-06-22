from __future__ import annotations

from pathlib import Path

from hh_automative.run_launcher import (
    PipelineLaunchRequest,
    available_resume_names,
    launch_pipeline,
)
from hh_automative.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        username="user",
        password="pass",
        search_profile="data-engineer",
        default_limit=10,
        dry_run=True,
        debug_browser=False,
        headless=True,
        timeout_seconds=10,
        chrome_user_data_dir=tmp_path / "chrome",
        cookies_path=tmp_path / "auth" / "cookies.json",
        local_storage_path=tmp_path / "auth" / "local_storage.json",
        state_db_path=tmp_path / "state.sqlite3",
        analytics_db_path=tmp_path / "analytics.duckdb",
        log_dir=tmp_path / "logs",
        diagnostics_dir=tmp_path / "diagnostics",
        resume_text_path=tmp_path / "resume.txt",
        use_llm=False,
        use_llm_cover_letter=False,
        use_llm_questionnaire=False,
        llm_provider="mistral",
        llm_fallbacks=[],
        llm_timeout_seconds=60,
        mistral_api_key="",
        mistral_model="mistral-small-latest",
        gemini_api_key="",
        gemini_model="gemini-1.5-flash",
        openrouter_api_key="",
        openrouter_model="mistralai/mistral-7b-instruct:free",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.1:8b",
        search_profiles_path=tmp_path / "search_profiles.json",
        resumes_path=tmp_path / "resumes.json",
    )


def test_available_resume_names_reads_config(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.resumes_path.write_text(
        '{"default_resume": "Teacher", "resume_codes": {"Teacher": "abc", "Cleaner": "def"}}',
        encoding="utf-8",
    )

    assert available_resume_names(settings) == ["Cleaner", "Teacher"]


def test_launch_pipeline_writes_runtime_configs_and_starts_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.resumes_path.write_text(
        '{"default_resume": "Teacher", "resume_codes": {"Teacher": "abc", "Cleaner": "def"}}',
        encoding="utf-8",
    )
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command, cwd, env, stdout, stderr):  # noqa: ANN001
        launched["command"] = command
        launched["cwd"] = cwd
        launched["env"] = env
        launched["stdout"] = stdout
        launched["stderr"] = stderr
        return FakeProcess()

    monkeypatch.setattr("hh_automative.run_launcher.subprocess.Popen", fake_popen)

    result = launch_pipeline(
        PipelineLaunchRequest(
            query="teacher",
            exclude="sales",
            resume_name="Teacher",
            limit=5,
            dry_run=True,
            label="Teacher run",
        ),
        settings=settings,
        cwd=tmp_path,
    )

    assert result.pid == 12345
    assert result.search_profiles_path.exists()
    assert result.resumes_path.exists()
    assert result.log_path.exists()
    assert "--target-new" in launched["command"]
    assert "--ignore-dry-run-history" in launched["command"]
    assert "--dry-run" in launched["command"]
    assert launched["env"]["HH_SEARCH_PROFILES_PATH"] == str(result.search_profiles_path)
    assert launched["env"]["HH_RESUMES_PATH"] == str(result.resumes_path)
    assert launched["env"]["HH_HEADLESS"] == "true"
