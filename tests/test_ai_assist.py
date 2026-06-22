from __future__ import annotations

from hh_automative.ai_assist import (
    VacancyQuestion,
    build_cover_letter_prompt,
    build_cover_letter_system_constraints,
    build_questionnaire_prompt,
    parse_json_payload,
    validate_cover_letter_grounding,
    validate_cover_letter_payload_shape,
    validate_questionnaire_answer_text,
)


def test_cover_letter_prompt_requests_json() -> None:
    prompt = build_cover_letter_prompt("Вакансия Data Engineer", "Резюме Python SQL")

    assert "cover_letter" in prompt
    assert "Верни только валидный JSON" in prompt
    assert "Вакансия Data Engineer" in prompt


def test_cover_letter_prompt_strict_mode_contains_specificity_rules() -> None:
    prompt = build_cover_letter_prompt("Вакансия Data Engineer", "Python SQL", strict=True)

    assert "placeholders" in prompt
    assert "свяжи" in prompt


def test_cover_letter_prompt_includes_hard_constraints() -> None:
    assert "СТРОГО:" in build_cover_letter_system_constraints()
    prompt = build_cover_letter_prompt("Вакансия Data Engineer", "Python SQL")
    assert "hard_constraints" in prompt



def test_cover_letter_prompt_can_include_vacancy_context() -> None:
    prompt = build_cover_letter_prompt(
        "Вакансия Data Engineer с акцентом на ETL",
        "Резюме с опытом Python и SQL",
        vacancy_context="Data Engineer | Acme Co | https://hh.ru/vacancy/123",
    )

    assert "Data Engineer | Acme Co" in prompt
    assert "vacancy_context" in prompt


def test_validate_cover_letter_payload_rejects_template_markers() -> None:
    errors = validate_cover_letter_payload_shape(
        {"cover_letter": "Уважаемый [Ваше имя], я заинтересован в вакансии [название должности]"}
    )

    assert errors


def test_validate_cover_letter_payload_rejects_too_short() -> None:
    errors = validate_cover_letter_payload_shape(
        {"cover_letter": "Кратко подтверждаю интерес и опыт работы с SQL."}
    )

    assert errors


def test_validate_cover_letter_payload_rejects_greeting_in_soft_mode() -> None:
    errors = validate_cover_letter_payload_shape(
        {"cover_letter": "Добрый день! Я занимался SQL и Python на проектах по ETL."},
        strict=False,
        vacancy_text="Нужен SQL инженер",
        resume_text="Опыт ETL на Python",
    )

    assert errors


def test_validate_cover_letter_payload_rejects_lack_of_context() -> None:
    errors = validate_cover_letter_grounding(
        {
            "cover_letter": (
                "Я считаю, что смогу принести пользу вашему отделу и выполнить поставленные цели. "
                "Имею опыт работы в проектной среде, умею выстраивать процессы и доводить задачи до результата. "
                "Буду готов к быстрому погружению в требования и совместной работе с командой."
            )
        },
        vacancy_text="Требуется Data Engineer со знанием SQL и Kafka.",
        resume_text="Ранее работал с Airflow, Python и SQL на проектах по ETL.",
    )

    assert errors
    assert any("vacancy and resume details" in error for error in errors)


def test_validate_cover_letter_payload_accepts_contextual_text() -> None:
    errors = validate_cover_letter_payload_shape(
        {
            "cover_letter": (
                "Я несколько лет строил пайплайны данных на Python и SQL, подключая Kafka к "
                "ETL для подготовки витрин в облаке. В вашей вакансии требуются экспертиза по "
                "SQL, Python и аналитическим потокам данных — этот опыт прямо соответствует."
            )
        },
        vacancy_text="Нужен Data Engineer для потоков данных на Python, SQL и Kafka.",
        resume_text="Python, SQL, построение ETL и потоков данных на Kafka в облаке.",
    )

    assert not errors


def test_validate_cover_letter_payload_rejects_if_not_enough_context_overlap() -> None:
    errors = validate_cover_letter_grounding(
        {
            "cover_letter": (
                "Мне интересна роль в вашей компании как шанс для личного роста и расширения практики: "
                "я всегда ценю возможность влиять на процессы, быстро понимать требования бизнеса и "
                "работать в команде с акцентом на качество, надежность и прозрачную коммуникацию "
                "на ежедневной основе для стабильных результатов."
            )
        },
        vacancy_text="Требуются навыки SQL и Python, желательно Airflow.",
        resume_text="У меня есть опыт работы с SQL и Python.",
    )
    assert any("vacancy and resume details" in error for error in errors)
    assert any("vacancy and resume details" in error for error in errors)


def test_validate_cover_letter_payload_does_not_block_grounding_mismatch() -> None:
    errors = validate_cover_letter_payload_shape(
        {
            "cover_letter": (
                "В вашей вакансии важны стабильные ETL/ELT пайплайны и работа с PostgreSQL, "
                "ClickHouse, Airflow — у меня есть опыт проектирования и поддержки таких решений. "
                "Я автоматизировал ETL-процессы, настраивал мониторинг потоков данных и работал "
                "с Python и SQL в production-среде."
            )
        },
        vacancy_text="Требуются SQL и Python.",
        resume_text="Опыт Greenplum и Data Vault.",
    )

    assert not errors


def test_validate_cover_letter_payload_rejects_placeholder_tokens() -> None:
    errors = validate_cover_letter_payload_shape({"cover_letter": "Привет, [candidate_name]"})

    assert any("placeholder" in error for error in errors)


def test_validate_cover_letter_payload_rejects_unsupported_numbers() -> None:
    errors = validate_cover_letter_payload_shape(
        {
            "cover_letter": (
                "Я проектировал ETL-конвейеры на Python и SQL, которые обрабатывали 100 ГБ "
                "данных в сутки, и сократил время реакции на инциденты на 30%. "
                "Также занимался мониторингом потоков данных, поддерживал стабильность загрузок "
                "и взаимодействовал с аналитиками при подготовке витрин."
            )
        },
        vacancy_text="Требуется Data Engineer с Python, SQL и Airflow.",
        resume_text="Опыт ETL, Python, SQL и мониторинга данных.",
    )

    assert any("numeric" in error for error in errors)


def test_validate_cover_letter_payload_allows_supported_numbers() -> None:
    errors = validate_cover_letter_payload_shape(
        {
            "cover_letter": (
                "Я проектировал ETL-конвейеры на Python и SQL и сокращал время настройки "
                "новых процессов на 40%, что полезно для задач по Airflow. "
                "Также занимался мониторингом потоков данных, поддерживал стабильность загрузок "
                "и взаимодействовал с аналитиками при подготовке витрин."
            )
        },
        vacancy_text="Требуется Data Engineer с Python, SQL и Airflow.",
        resume_text="Опыт ETL, Python, SQL. Сократил время настройки процессов на 40%.",
    )

    assert not errors


def test_questionnaire_prompt_contains_options_contract() -> None:
    prompt = build_questionnaire_prompt(
        "Вакансия",
        "Резюме",
        [
            VacancyQuestion(
                question_id="q1",
                label="Есть ли опыт SQL?",
                input_type="radio",
                options=["Да", "Нет"],
            )
        ],
    )

    assert "selected_options" in prompt
    assert '"options": [' in prompt
    assert "Да" in prompt
    assert "без приветствий" in prompt


def test_validate_questionnaire_answer_text_rejects_cover_letter_shape() -> None:
    errors = validate_questionnaire_answer_text(
        {
            "answers": [
                {
                    "question_id": "q1",
                    "answer_text": (
                        "Уважаемые коллеги! Благодарю за возможность откликнуться. "
                        "Мой опыт SQL и Python подходит. С уважением, Ирина Яковлева"
                    ),
                    "selected_options": [],
                }
            ]
        },
        vacancy_text="Требуется SQL и Python.",
        resume_text="Опыт SQL и Python.",
    )

    assert any("cover letter" in error for error in errors)


def test_validate_questionnaire_answer_text_rejects_unsupported_numbers() -> None:
    errors = validate_questionnaire_answer_text(
        {
            "answers": [
                {
                    "question_id": "q1",
                    "answer_text": "Работал с SQL и Python, обрабатывал 100 ТБ данных.",
                    "selected_options": [],
                }
            ]
        },
        vacancy_text="Требуется SQL и Python.",
        resume_text="Опыт SQL и Python.",
    )

    assert any("numeric" in error for error in errors)


def test_prompts_include_vacancy_and_resume_payload() -> None:
    vacancy_text = "Вакансия: Data Engineer. Требуется SQL, Python, Kafka."
    resume_text = "Резюме: 5 лет в Python и SQL, проекты с ETL и аналитикой."
    cover_prompt = build_cover_letter_prompt(vacancy_text, resume_text)
    questionnaire_prompt = build_questionnaire_prompt(
        vacancy_text,
        resume_text,
        [
            VacancyQuestion(
                question_id="q1",
                label="Почему вы хотите работать в этой компании?",
                input_type="textarea",
                options=[],
            )
        ],
    )

    assert vacancy_text in cover_prompt
    assert resume_text in cover_prompt
    assert vacancy_text in questionnaire_prompt
    assert resume_text in questionnaire_prompt


def test_parse_json_payload_accepts_plain_json() -> None:
    result = parse_json_payload('{"cover_letter": "Привет"}')

    assert result == {"cover_letter": "Привет"}
