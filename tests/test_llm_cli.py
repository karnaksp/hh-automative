from __future__ import annotations

import json
from pathlib import Path

import pytest

from hh_automative.errors import LLMProviderError
from hh_automative.llm import LLMAnswer
from hh_automative.llm_cli import llm_cover_letter, llm_questionnaire


class StaticClient:
    provider_name = "static"

    def __init__(self, parsed_json: dict[str, object]) -> None:
        self.parsed_json = parsed_json

    def ask_json(self, _prompt: str, _timeout_seconds: int) -> LLMAnswer:
        return LLMAnswer(
            raw_text=json.dumps(self.parsed_json, ensure_ascii=False),
            parsed_json=self.parsed_json,
            provider=self.provider_name,
            model="test",
        )


def test_llm_cover_letter_prompt_ready(monkeypatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    vacancy_path = tmp_path / "vacancy.txt"
    vacancy_path.write_text("Вакансия Data Engineer", encoding="utf-8")

    result = llm_cover_letter(vacancy_path, ask=False)

    assert result["status"] == "prompt_ready"
    assert "cover_letter" in str(result["prompt"])
    assert "Вакансия Data Engineer" in str(result["prompt"])


def test_llm_cover_letter_ask_validates_payload(monkeypatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    vacancy_path = tmp_path / "vacancy.txt"
    vacancy_path.write_text("Вакансия Data Engineer", encoding="utf-8")
    monkeypatch.setattr(
        "hh_automative.llm_cli.create_llm_client",
        lambda _settings: StaticClient({"cover_letter": ""}),
    )

    with pytest.raises(LLMProviderError, match="cover_letter"):
        llm_cover_letter(vacancy_path, ask=True)


def test_llm_questionnaire_prompt_ready(monkeypatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    vacancy_path = tmp_path / "vacancy.txt"
    vacancy_path.write_text("Вакансия Data Engineer", encoding="utf-8")
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "q1",
                    "label": "Есть опыт SQL?",
                    "input_type": "radio",
                    "options": ["Да", "Нет"],
                    "required": True,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = llm_questionnaire(vacancy_path, questions_path, ask=False)

    assert result["status"] == "prompt_ready"
    assert "selected_options" in str(result["prompt"])
    assert "Есть опыт SQL?" in str(result["prompt"])


def test_llm_questionnaire_ask_validates_required_answers(monkeypatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    vacancy_path = tmp_path / "vacancy.txt"
    vacancy_path.write_text("Вакансия Data Engineer", encoding="utf-8")
    questions_path = _questions_file(tmp_path)
    monkeypatch.setattr(
        "hh_automative.llm_cli.create_llm_client",
        lambda _settings: StaticClient({"answers": []}),
    )

    with pytest.raises(LLMProviderError, match="required question"):
        llm_questionnaire(vacancy_path, questions_path, ask=True)


def test_llm_questionnaire_ask_returns_answered(monkeypatch, tmp_path: Path) -> None:
    _prepare_env(monkeypatch, tmp_path)
    vacancy_path = tmp_path / "vacancy.txt"
    vacancy_path.write_text("Вакансия Data Engineer", encoding="utf-8")
    questions_path = _questions_file(tmp_path)
    monkeypatch.setattr(
        "hh_automative.llm_cli.create_llm_client",
        lambda _settings: StaticClient(
            {
                "answers": [
                    {"question_id": "q1", "answer_text": "", "selected_options": ["Да"]}
                ],
                "needs_human_review": False,
            }
        ),
    )

    result = llm_questionnaire(vacancy_path, questions_path, ask=True)

    assert result["status"] == "answered"
    assert result["provider"] == "static"


def _prepare_env(monkeypatch, tmp_path: Path) -> None:
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text("Резюме Python SQL", encoding="utf-8")
    monkeypatch.setenv("HH_RESUME_TEXT_PATH", str(resume_path))
    monkeypatch.setenv("HH_ANALYTICS_DB_PATH", str(tmp_path / "analytics.duckdb"))
    monkeypatch.setenv("HH_STATE_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("HH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("HH_DIAGNOSTICS_DIR", str(tmp_path / "diagnostics"))
    monkeypatch.setenv("HH_USE_LLM", "true")


def _questions_file(tmp_path: Path) -> Path:
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            [
                {
                    "question_id": "q1",
                    "label": "Есть опыт SQL?",
                    "input_type": "radio",
                    "options": ["Да", "Нет"],
                    "required": True,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return questions_path
