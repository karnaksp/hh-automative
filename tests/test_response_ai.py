from __future__ import annotations

from pathlib import Path

import pytest

from hh_automative.ai_assist import VacancyQuestion
from hh_automative.errors import LLMProviderError
from hh_automative.hh_form import FormField
from hh_automative.models import Vacancy
from hh_automative.response import (
    ResponseAutomation,
    _cover_letter_text,
    _fill_cover_letter_if_available,
    _handle_questions_if_present,
    _is_submit_label,
    _normalize_cover_letter_text,
    _resolve_resume_text,
)
from hh_automative.settings import Settings


class FailingLLM:
    def ask_json(self, _prompt: str, timeout_seconds: int):
        raise LLMProviderError(f"failed after {timeout_seconds}")


class StaticLLM:
    def __init__(self, parsed_json: dict[str, object]) -> None:
        self.parsed_json = parsed_json

    def ask_json(self, _prompt: str, timeout_seconds: int):
        return type(
            "Answer",
            (),
            {"raw_text": str(self.parsed_json), "parsed_json": self.parsed_json},
        )()


class RetriesThenValidLLM:
    def __init__(self, first: dict[str, object], second: dict[str, object]) -> None:
        self.first = first
        self.second = second
        self.calls = 0

    def ask_json(self, _prompt: str, timeout_seconds: int):
        self.calls += 1
        payload = self.first if self.calls == 1 else self.second
        return type(
            "Answer",
            (),
            {"raw_text": str(payload), "parsed_json": payload},
        )()


class CapturePromptLLM:
    def __init__(self) -> None:
        self.prompt = ""

    def ask_json(self, prompt: str, timeout_seconds: int):
        self.prompt = prompt
        return type(
            "Answer",
            (),
            {
                "raw_text": '{"cover_letter": "Я работал с SQL и Python и это напрямую помогает на этой вакансии."}',
                "parsed_json": {
                    "cover_letter": "Я работал с SQL и Python и это напрямую помогает на этой вакансии."
                },
            },
        )()


class PromptRetryLLM:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.prompts: list[str] = []
        self.responses = responses
        self.calls = 0

    def ask_json(self, prompt: str, timeout_seconds: int):
        self.calls += 1
        index = min(self.calls - 1, len(self.responses) - 1)
        payload = self.responses[index]
        self.prompts.append(prompt)
        return type(
            "Answer",
            (),
            {"raw_text": str(payload), "parsed_json": payload},
        )()


def test_cover_letter_fallback_prompt_becomes_non_strict(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/10", vacancy_id="10", title="Data Engineer")
    analytics = RecordingAnalytics()
    llm = PromptRetryLLM(
        [
            {"cover_letter": "Уважаемый [Ваше имя], ..."},
            {
                "cover_letter": (
                    "Вакансия Data Engineer требует работы с SQL, Kafka и надежной обработкой данных в "
                    "производственной среде. В моем резюме отражен практический опыт построения ETL "
                    "процессов на Python и SQL для крупных датасетов, а также участие в задачах автоматизации "
                    "загрузок и поддержки пайплайнов в облачной инфраструктуре."
                )
            },
        ]
    )
    automation = ResponseAutomation(llm=llm, analytics=analytics, profile_name="data-engineer")

    result = _cover_letter_text(
        object(),
        settings,
        vacancy,
        (
            "Требуется Data Engineer: SQL, Python и Kafka для настройки и сопровождения потоков данных "
            "в production с сильным вниманием к устойчивости."
        ),
        automation,
        (
            "Я проектировал и поддерживал ETL и ELT процессы на Python и SQL, запускал потоки "
            "обработки событий в Kafka и внедрял мониторинг задач."
        ),
    )

    assert len(llm.prompts) == 2
    assert "placeholders" in llm.prompts[0]
    assert "hard_constraints" in llm.prompts[0]
    assert "Уважаемый" not in result
    assert llm.calls == 2
    assert [event["status"] for event in analytics.events][0:2] == ["prompt_submitted", "failed"]
    assert analytics.events[-1]["status"] == "answered"


class RecordingAnalytics:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def ai_assist_event(self, **kwargs) -> None:
        self.events.append(kwargs)


def test_questionnaire_llm_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/1", vacancy_id="1", title="Data Engineer")
    analytics = RecordingAnalytics()
    automation = ResponseAutomation(
        llm=FailingLLM(),
        analytics=analytics,
        profile_name="data-engineer",
    )
    fields = [
        FormField(
            question=VacancyQuestion("q1", "Есть опыт SQL?", "radio", ["Да", "Нет"]),
            element=object(),
            option_elements={},
        )
    ]
    monkeypatch.setattr("hh_automative.response.extract_response_questions", lambda _context: fields)

    with pytest.raises(LLMProviderError):
        _handle_questions_if_present(object(), settings, vacancy, "vacancy text", automation)

    assert [event["status"] for event in analytics.events] == ["prompt_submitted", "failed"]
    assert analytics.events[-1]["task_type"] == "questionnaire"
    assert "failed after 5" in str(analytics.events[-1]["error_reason"])


def test_cover_letter_llm_failure_is_logged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/1", vacancy_id="1", title="Data Engineer")
    analytics = RecordingAnalytics()
    automation = ResponseAutomation(
        llm=FailingLLM(),
        analytics=analytics,
        profile_name="data-engineer",
    )

    result = _cover_letter_text(object(), settings, vacancy, "vacancy text", automation)

    assert result == ""
    assert [event["status"] for event in analytics.events] == ["prompt_submitted", "failed"]
    assert analytics.events[-1]["task_type"] == "cover_letter"
    assert "failed after 5" in str(analytics.events[-1]["error_reason"])


def test_questionnaire_invalid_llm_payload_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/1", vacancy_id="1", title="Data Engineer")
    analytics = RecordingAnalytics()
    automation = ResponseAutomation(
        llm=StaticLLM({"unexpected": []}),
        analytics=analytics,
        profile_name="data-engineer",
    )
    fields = [
        FormField(
            question=VacancyQuestion("q1", "Есть опыт SQL?", "radio", ["Да", "Нет"]),
            element=object(),
            option_elements={},
        )
    ]
    monkeypatch.setattr("hh_automative.response.extract_response_questions", lambda _context: fields)

    with pytest.raises(LLMProviderError):
        _handle_questions_if_present(object(), settings, vacancy, "vacancy text", automation)

    assert [event["status"] for event in analytics.events] == ["prompt_submitted", "failed"]
    assert "answers list" in str(analytics.events[-1]["error_reason"])


def test_questionnaire_missing_required_answer_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/1", vacancy_id="1", title="Data Engineer")
    analytics = RecordingAnalytics()
    automation = ResponseAutomation(
        llm=StaticLLM({"answers": []}),
        analytics=analytics,
        profile_name="data-engineer",
    )
    fields = [
        FormField(
            question=VacancyQuestion("q1", "Есть опыт SQL?", "radio", ["Да", "Нет"]),
            element=object(),
            option_elements={},
        )
    ]
    monkeypatch.setattr("hh_automative.response.extract_response_questions", lambda _context: fields)

    with pytest.raises(LLMProviderError):
        _handle_questions_if_present(object(), settings, vacancy, "vacancy text", automation)

    assert [event["status"] for event in analytics.events] == ["prompt_submitted", "failed"]
    assert "required question" in str(analytics.events[-1]["error_reason"])


def test_cover_letter_empty_llm_payload_is_logged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/1", vacancy_id="1", title="Data Engineer")
    analytics = RecordingAnalytics()
    automation = ResponseAutomation(
        llm=StaticLLM({"cover_letter": ""}),
        analytics=analytics,
        profile_name="data-engineer",
    )

    result = _cover_letter_text(object(), settings, vacancy, "vacancy text", automation)

    assert result == ""
    assert [event["status"] for event in analytics.events] == [
        "prompt_submitted",
        "failed",
        "prompt_submitted",
        "failed",
    ]
    assert "cover_letter" in str(analytics.events[-1]["error_reason"])


def test_normalize_cover_letter_text_removes_salutation_and_signature() -> None:
    raw = "\n\nУважаемый,\nСопровождение роли интересно!\nС уважением, Иван"

    normalized = _normalize_cover_letter_text(raw)

    assert normalized == "Сопровождение роли интересно!"


def test_normalize_cover_letter_text_removes_placeholders() -> None:
    raw = "Уважаемый [Имя], буду рад пообщаться на тему вакансии [название роли]."

    normalized = _normalize_cover_letter_text(raw)

    assert "[" not in normalized
    assert "[" not in normalized
    assert normalized.strip().lower().startswith("буду рад")


def test_cover_letter_retry_with_stricter_prompt(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/1", vacancy_id="1", title="Data Engineer")
    analytics = RecordingAnalytics()
    llm = RetriesThenValidLLM(
        first={"cover_letter": "Уважаемый [Ваше имя], ..."},
        second={
            "cover_letter": (
                "Мне интересна вакансия Data Engineer, потому что она напрямую связана с тем, "
                "чем я занимался последние годы: проектированием потоков данных и построением надежных ETL-процессов. "
                "В моем резюме зафиксирован опыт работы с SQL и Python на проектах, где требовалось обрабатывать "
                "объемные датасеты и строить регулярную загрузку данных в аналитические витрины. "
                "Также я применял Docker и облачные среды для автоматизации задач, что помогало уменьшить время "
                "выполнения отчетных пайплайнов и повысить устойчивость решений."
            )
        },
    )
    automation = ResponseAutomation(
        llm=llm,
        analytics=analytics,
        profile_name="data-engineer",
    )

    result = _cover_letter_text(
        object(),
        settings,
        vacancy,
        (
            "Требуется Data Engineer: SQL, Python, Kafka, ETL пайплайны в облачной инфраструктуре и "
            "поддержка production-критичных решений по обработке данных."
        ),
        automation,
        "Опыт работы с SQL, Python, Kafka и ETL на проектах в облаке.",
    )

    assert "[" not in result
    assert "]" not in result
    assert "Data Engineer" in result
    assert len(analytics.events) >= 4
    assert analytics.events[0]["status"] == "prompt_submitted"
    assert analytics.events[1]["status"] == "failed"
    assert analytics.events[-1]["status"] == "answered"


def test_cover_letter_prompt_receives_vacancy_context(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(
        url="https://hh.ru/vacancy/2",
        vacancy_id="2",
        title="Senior Data Engineer",
        company="Acme LLC",
    )
    llm = CapturePromptLLM()
    automation = ResponseAutomation(llm=llm, analytics=None, profile_name="data")

    result = _cover_letter_text(
        object(),
        settings,
        vacancy,
        (
            "Требуется Data Engineer с навыками SQL, Python и архитектуры ETL в production."
        ),
        automation,
        "Опыт работы с SQL и Python на больших данных.",
    )

    assert "Acme LLC" in llm.prompt
    assert "Senior Data Engineer" in llm.prompt
    assert "vacancy_context" in llm.prompt
    assert result


def test_cover_letter_falls_back_to_local_text_when_llm_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.use_llm_cover_letter = False
    vacancy = Vacancy(url="https://hh.ru/vacancy/3", vacancy_id="3", title="Data Engineer")
    automation = ResponseAutomation(llm=None, analytics=None, profile_name="data")

    result = _cover_letter_text(
        object(),
        settings,
        vacancy,
        (
            "Нужен Data Engineer со знанием SQL и Python для настройки и поддержки ETL-процессов."
        ),
        automation,
        "В резюме указаны SQL, Python, Docker и автоматизация ETL-конвейеров.",
    )

    assert result == ""


def test_submit_label_detector_covers_submit_variants() -> None:
    assert _is_submit_label("Отправить отклик")
    assert _is_submit_label("Откликнуться на вакансию")


def test_submit_label_detector_excludes_non_submit() -> None:
    assert _is_submit_label("Send response")
    assert not _is_submit_label("Загрузить резюме")


def test_cover_letter_ai_events_include_text_metadata(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/2", vacancy_id="2", title="Data Engineer")
    analytics = RecordingAnalytics()
    automation = ResponseAutomation(
        llm=StaticLLM(
            {
                "cover_letter": (
                    "Я работал с SQL и Python в проектах по автоматизации аналитических потоков, "
                    "в том числе строил пайплайны для агрегации данных и готовил витрины для продуктовых команд. "
                    "Это напрямую соответствует требованиям, где запрашивается опыт с большими массивами и устойчивыми процессами обработки."
                ),
            }
        ),
        analytics=analytics,
        profile_name="data-engineer",
    )

    result = _cover_letter_text(
        object(),
        settings,
        vacancy,
        "Вакансия Data Engineer: требуется SQL, Python, Airflow, Docker и опыт проектирования пайплайнов.",
        automation,
        resume_text=(
            "Я проектировал пайплайны данных на Python и SQL для обработки отчетов продаж, "
            "строил ETL для обновления витрин и участвовал в миграциях данных в облако."
        ),
        )

    assert "SQL" in result and "Python" in result
    assert analytics.events, "Cover letter flow should emit AI events"
    last_event = analytics.events[-1]
    metadata = last_event["metadata"]
    assert (
        metadata["vacancy_text"]
        == "Вакансия Data Engineer: требуется SQL, Python, Airflow, Docker и опыт проектирования пайплайнов."
    )
    assert (
        metadata["resume_text"]
        == "Я проектировал пайплайны данных на Python и SQL для обработки отчетов продаж, строил ETL для обновления витрин и участвовал в миграциях данных в облако."
    )


def test_fill_cover_letter_logs_not_available_when_textarea_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    vacancy = Vacancy(url="https://hh.ru/vacancy/4", vacancy_id="4", title="Data Engineer")
    analytics = RecordingAnalytics()
    automation = ResponseAutomation(llm=None, analytics=analytics, profile_name="data-engineer")

    monkeypatch.setattr("hh_automative.response._open_cover_letter_editor", lambda _context: None)
    monkeypatch.setattr("hh_automative.response.scroll_to_bottom", lambda _context: None)
    monkeypatch.setattr("hh_automative.response.extract_cover_letter_field", lambda _context: None)

    _fill_cover_letter_if_available(
        object(),
        settings,
        vacancy,
        "vacancy text",
        automation,
        "resume text",
    )

    assert analytics.events[-1]["status"] == "not_available"
    assert "textarea was not found" in str(analytics.events[-1]["error_reason"])


def test_resolve_resume_text_prefers_cached_text(tmp_path: Path) -> None:
    fallback_text = "fallback"
    assert _resolve_resume_text(
        "resume_cached", {"resume_cached": "cached text"}, fallback_text
    ) == "cached text"
    assert _resolve_resume_text("resume_missing", {}, fallback_text) == fallback_text


def _settings(tmp_path: Path) -> Settings:
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text("resume text", encoding="utf-8")
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
        resume_text_path=resume_path,
        use_llm=True,
        use_llm_cover_letter=True,
        use_llm_questionnaire=True,
        llm_provider="gemini",
        llm_fallbacks=[],
        llm_timeout_seconds=5,
        mistral_api_key="",
        mistral_model="mistral-small-latest",
        gemini_api_key="",
        gemini_model="gemini-1.5-flash",
        openrouter_api_key="",
        openrouter_model="mistralai/mistral-7b-instruct:free",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.1:8b",
    )
