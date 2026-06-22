"""Prompt contracts for API/browser-agnostic LLM assistance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class AssistTaskType(StrEnum):
    COVER_LETTER = "cover_letter"
    QUESTIONNAIRE = "questionnaire"


@dataclass(slots=True)
class VacancyQuestion:
    question_id: str
    label: str
    input_type: str
    options: list[str]
    required: bool = True


def load_resume_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_cover_letter_prompt(
    vacancy_text: str,
    resume_text: str,
    strict: bool = False,
    vacancy_context: str = "",
) -> str:
    normalized_vacancy_text = vacancy_text.strip()
    normalized_resume_text = resume_text.strip()
    return _prompt(
        task="Подготовь правдоподобное сопроводительное письмо для отклика на вакансию.",
        expected_schema={
            "cover_letter": "строка, готовый текст письма на русском языке",
            "confidence": "число от 0 до 1",
            "notes": "коротко, почему письмо подходит вакансии",
        },
        payload={
            "vacancy_text": normalized_vacancy_text,
            "vacancy_context": vacancy_context,
            "resume_text": normalized_resume_text,
            "hard_constraints": build_cover_letter_system_constraints(),
            "rules": _cover_letter_rules(strict),
            "examples": _cover_letter_examples(),
        },
    )


def build_cover_letter_system_constraints() -> str:
    return "\n".join(
        [
            "СТРОГО: не добавляй служебные приветствия/подписи и не выводи текст в виде шаблона.",
            "Сразу после генерации проверяй: текст должен быть связным, без placeholder-меток в скобках и без placeholders вида [..]",
            "Тон: живой, конкретный, без канцелярщины.",
        ]
    )


def _cover_letter_rules(strict: bool) -> list[str]:
    rules = [
        "Пиши от первого лица.",
        "Не выдумывай опыт, которого нет в резюме.",
        "Тон: деловой, человеческий, краткий, уверенный.",
        "Используй только информацию из vacancy_text и resume_text.",
        "Не упоминай платформу/сервис, не пиши от имени бота или API.",
        "Структура: 1 короткий вступительный абзац (1-2 предложения), "
        "1 блок про совпадение требований и опыта (2-3 предложения), "
        "1 блок про пользу для компании (1-2 предложения).",
        "Длина: 500-1300 символов.",
        "Обязательно упомяни 2-3 конкретных факта из резюме и 2-3 конкретных требования/детали вакансии.",
        "Не добавляй числа, проценты, сроки, зарплаты, объемы данных и другие метрики, если они не указаны явно в vacancy_text или resume_text.",
        "Верни только валидный JSON без markdown.",
        "Не добавляй шаблонные приветствия и не включай подпись (например, «Уважаемый», «Здравствуйте», «Добрый день», «С уважением»).",
    ]
    if strict:
        rules.extend(
            [
                "Не используй placeholders и заготовки: [Ваше имя], [название должности], [название компании], [кандидат].",
                "Не начинай и не заканчивай письмо формальной рамкой и не пиши служебные приветствия: «Здравствуйте», «Добрый день», «Доброй ночи», «Привет».",
                "Пиши письмо конкретно под вакансию: свяжи 1-2 требования вакансии с 1-2 доказуемыми фактами из резюме.",
                "Не оставляй заготовки без персонализации — каждая мысль должна опираться на текст вакансии и резюме.",
                "Упоминай в тексте минимум два конкретных факта из резюме и две конкретные детали вакансии.",
                "Если есть возможность, укажи, почему этот конкретный опыт релевантен именно задачам вакансии.",
                "Избегай общих фраз вроде «Я очень заинтересован в этой вакансии», «Я люблю работать в команде» без привязки к фактам.",
            ]
        )
    return rules


def _cover_letter_examples() -> list[dict[str, str]]:
    return [
        {
            "good_example": (
                "На прошлых проектах в роли Data Engineer я выстраивал пайплайны загрузки данных на Python и SQL, "
                "а также автоматизировал мониторинг для уменьшения времени инцидентов в продакшене. "
                "В вашей вакансии указаны задачи по обработке данных и ETL, поэтому мой практический опыт "
                "применяется прямо: я уже проектировал похожие потоки и доводил их до стабильной работы."
            ),
            "bad_example": (
                "Уважаемый, мне очень интересна ваша вакансия, и я хочу присоединиться к команде. "
                "Буду рад обсудить детали."
            ),
        }
    ]

def build_questionnaire_prompt(
    vacancy_text: str,
    resume_text: str,
    questions: list[VacancyQuestion],
) -> str:
    return _prompt(
        task="Подготовь структурированные ответы на вопросы работодателя перед откликом.",
        expected_schema={
            "answers": [
                {
                    "question_id": "id вопроса из входных данных",
                    "answer_text": "текст для textarea/input или пустая строка",
                    "selected_options": ["точные labels выбранных checkbox/radio/select options"],
                    "confidence": "число от 0 до 1",
                    "reason": "короткое объяснение выбора",
                }
            ],
            "needs_human_review": "boolean",
        },
        payload={
            "vacancy_text": vacancy_text,
            "resume_text": resume_text,
            "questions": [question_to_dict(question) for question in questions],
            "rules": [
                "Не выдумывай опыт, которого нет в резюме.",
                "Отвечай прямо на каждый вопрос, без приветствий, подписей и сопроводительного письма.",
                "Не добавляй имя кандидата, если вопрос явно не просит имя.",
                "Не добавляй числа, проценты, сроки, зарплаты, объемы данных и другие метрики, если они не указаны явно в vacancy_text или resume_text.",
                "Для checkbox/radio/select выбирай только из options.",
                "Если уверенности мало, поставь needs_human_review=true.",
                "Верни только валидный JSON без markdown.",
            ],
        },
    )


def parse_json_payload(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    return json.loads(cleaned)


def validate_questionnaire_payload_shape(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "answers" not in payload or not isinstance(payload["answers"], list):
        return ["LLM response does not contain answers list."]
    for index, answer in enumerate(payload["answers"], start=1):
        if not isinstance(answer, dict):
            errors.append(f"LLM answer #{index} is not an object.")
            continue
        selected_options = answer.get("selected_options", [])
        if selected_options is not None and not isinstance(selected_options, list):
            errors.append(f"LLM answer #{index} selected_options must be a list.")
    return errors


def validate_questionnaire_answer_text(
    payload: dict[str, Any],
    vacancy_text: str = "",
    resume_text: str = "",
) -> list[str]:
    errors: list[str] = []
    answers = payload.get("answers", [])
    if not isinstance(answers, list):
        return ["LLM response does not contain answers list."]
    for index, answer in enumerate(answers, start=1):
        if not isinstance(answer, dict):
            continue
        text = str(answer.get("answer_text", "")).strip()
        if not text:
            continue
        if _contains_questionnaire_letter_patterns(text):
            errors.append(
                f"LLM answer #{index} looks like a cover letter/signature, not a direct answer."
            )
        if _contains_unsupported_numeric_claims(text, vacancy_text, resume_text):
            errors.append(f"LLM answer #{index} contains unsupported numeric claims.")
    return errors


def validate_cover_letter_payload_shape(
    payload: dict[str, Any],
    vacancy_text: str = "",
    resume_text: str = "",
    *,
    strict: bool = True,
) -> list[str]:
    cover_letter = str(payload.get("cover_letter", "")).strip()
    if not cover_letter:
        return ["LLM response does not contain non-empty cover_letter."]
    if _contains_placeholders(cover_letter):
        return ["LLM response contains placeholder-like text in cover_letter."]
    if _too_short(cover_letter, strict=strict):
        return ["LLM cover_letter is too short for a meaningful personal letter."]
    if _contains_generic_template_patterns(cover_letter, strict=True):
        return ["LLM response appears to be a template and not personalized."]
    if _contains_unsupported_numeric_claims(cover_letter, vacancy_text, resume_text):
        return ["LLM cover_letter contains unsupported numeric claims."]
    return []


def validate_cover_letter_grounding(
    payload: dict[str, Any],
    vacancy_text: str = "",
    resume_text: str = "",
    *,
    strict: bool = True,
) -> list[str]:
    cover_letter = str(payload.get("cover_letter", "")).strip()
    if not cover_letter:
        return ["LLM response does not contain non-empty cover_letter."]
    if not _references_source_context(cover_letter, vacancy_text, resume_text, strict=strict):
        return [
            "LLM cover_letter does not reference vacancy and resume details (vacancy AND resume)."
        ]
    return []


def _contains_placeholders(text: str) -> bool:
    return bool(re.search(r"\[[^\]]{2,80}\]", text))


def _contains_generic_template_patterns(text: str, strict: bool) -> bool:
    lowered = text.casefold()
    patterns = [
        "ваше имя",
        "название должности",
        "название компании",
        "current position",
        "your name",
        "ваше письмо",
        "меня зовут",
        "с уважением",
    ]
    if strict:
        patterns.extend(
            [
                "уважаемый",
                "уважаемая",
                "здравствуйте",
                "добрый день",
                "доброй ночи",
                "добрый вечер",
                "привет",
                "dear",
                "sincerely",
                "best regards",
                "с уважением",
            ]
        )
    if not strict:
        patterns.extend(["уверен", "считаю, что"])
    greeting_prefix_patterns = [
        r"\A(доброе|добрый|доброй|здравствуйте|привет|приветствую|уважаемый|уважаемая)\b",
        r"\n(доброе|добрый|доброй|здравствуйте|привет|приветствую|уважаемый|уважаемая)\b",
    ]
    for pattern in greeting_prefix_patterns:
        if re.search(pattern, lowered, re.IGNORECASE):
            return True
    if re.search(r"\[[^\]]{2,40}\]", text):
        return True
    return any(pattern in lowered for pattern in patterns)


def _contains_questionnaire_letter_patterns(text: str) -> bool:
    lowered = text.casefold()
    patterns = [
        "уважаемые коллеги",
        "благодарю за возможность откликнуться",
        "с уважением",
        "готов обсудить детали",
        "вакансию в вашу команду",
    ]
    if any(pattern in lowered for pattern in patterns):
        return True
    return bool(
        re.search(
            r"\A(доброе|добрый|доброй|здравствуйте|привет|приветствую|уважаемый|уважаемая)\b",
            lowered,
            re.IGNORECASE,
        )
    )


def _contains_unsupported_numeric_claims(
    cover_letter: str,
    vacancy_text: str,
    resume_text: str,
) -> bool:
    source = f"{vacancy_text}\n{resume_text}"
    source_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", source))
    for number in re.findall(r"\d+(?:[.,]\d+)?", cover_letter):
        if number not in source_numbers:
            return True
    return False


def _too_short(text: str, strict: bool) -> bool:
    normalized = re.sub(r"\s+", "", text)
    limit = 150 if strict else 40
    return len(normalized) < limit


def _references_source_context(
    cover_letter: str,
    vacancy_text: str,
    resume_text: str,
    *,
    strict: bool = True,
) -> bool:
    vacancy_tokens = _extract_context_tokens(vacancy_text)
    resume_tokens = _extract_context_tokens(resume_text)
    required_matches = 2 if strict else 1
    has_vacancy = _has_overlap(
        cover_letter,
        vacancy_tokens,
        required_matches=required_matches,
    )
    has_resume = _has_overlap(
        cover_letter,
        resume_tokens,
        required_matches=required_matches,
    )
    return bool(has_vacancy and has_resume)


def _extract_context_tokens(text: str) -> set[str]:
    if not text:
        return set()
    words = re.findall(r"[А-Яа-яA-Za-z]{4,}", text.lower())
    stopwords = {
        "вакансия",
        "резюме",
        "опыт",
        "работе",
        "работа",
        "обязанности",
        "компания",
        "компании",
        "должность",
        "требования",
        "требований",
        "данный",
        "данной",
        "выполнения",
        "находиться",
        "требовать",
        "готов",
        "готова",
    }
    normalized = {
        _normalize_token(word)
        for word in words
        if _normalize_token(word) not in stopwords
    }
    return {word for word in normalized if len(word) >= 4}


def _has_overlap(text: str, expected: set[str], *, required_matches: int = 1) -> bool:
    if not expected:
        return text.strip() != ""
    normalized_text = set(_extract_context_tokens(text))
    if not normalized_text:
        return False
    matches = sum(1 for token in expected if token in normalized_text)
    return matches >= required_matches


def _normalize_token(value: str) -> str:
    return re.sub(r"[^\wа-яА-Я]", "", value).casefold()


def question_to_dict(question: VacancyQuestion) -> dict[str, Any]:
    return {
        "question_id": question.question_id,
        "label": question.label,
        "input_type": question.input_type,
        "options": question.options,
        "required": question.required,
    }


def _prompt(task: str, expected_schema: dict[str, Any], payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            task,
            "",
            "Нужно вернуть строго JSON по схеме:",
            json.dumps(expected_schema, ensure_ascii=False, indent=2),
            "",
            "Входные данные:",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )
