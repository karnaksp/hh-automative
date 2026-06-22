from __future__ import annotations

from pathlib import Path

from hh_automative.resume_sync import load_cached_resume_text, refresh_resume_text_cache


def test_load_cached_resume_text_returns_empty_if_missing(tmp_path: Path) -> None:
    cache_dir = tmp_path / "resumes"
    cache_dir.mkdir()

    assert load_cached_resume_text(cache_dir, "resume_abc") == ""


def test_load_cached_resume_text_reads_existing_file(tmp_path: Path) -> None:
    cache_dir = tmp_path / "resumes"
    cache_dir.mkdir()
    (cache_dir / "resume-abc.txt").write_text("мое резюме", encoding="utf-8")

    assert load_cached_resume_text(cache_dir, "resume-abc") == "мое резюме"


def test_refresh_resume_text_cache_applies_temporary_page_load_timeout(
    monkeypatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "resumes"
    cache_dir.mkdir()
    calls: list[int] = []

    class FakeDriver:
        def set_page_load_timeout(self, seconds: int) -> None:
            calls.append(seconds)

    class FakeContext:
        driver = FakeDriver()

    monkeypatch.setattr(
        "hh_automative.resume_sync.sync_resume_texts",
        lambda context, resume_codes, cache_dir: {"synced": 1, "saved": ["abc"], "texts": {}},
    )

    result = refresh_resume_text_cache(
        FakeContext(),
        {"Data engineer": "abc"},
        cache_dir,
        page_load_timeout_seconds=17,
    )

    assert result["synced"] == 1
    assert calls == [17, 300]
