"""CLI helpers for API-based LLM workflows."""

from __future__ import annotations

import json
from pathlib import Path

from hh_automative.ai_assist import (
    VacancyQuestion,
    build_cover_letter_prompt,
    build_questionnaire_prompt,
    load_resume_text,
    validate_cover_letter_payload_shape,
    validate_questionnaire_payload_shape,
)
from hh_automative.analytics import DuckDBAnalytics, new_run_id
from hh_automative.errors import LLMProviderError
from hh_automative.hh_form import FormField, validate_structured_answers
from hh_automative.llm import create_llm_client
from hh_automative.settings import Settings


def llm_cover_letter(vacancy_text_path: Path, ask: bool = False) -> dict[str, object]:
    settings = Settings.load()
    settings.ensure_dirs()
    vacancy_text = vacancy_text_path.read_text(encoding="utf-8").strip()
    resume_text = load_resume_text(settings.resume_text_path)
    prompt = build_cover_letter_prompt(vacancy_text, resume_text)
    analytics = DuckDBAnalytics(settings.analytics_db_path, new_run_id())
    try:
        if not ask:
            analytics.ai_assist_event(
                task_type="cover_letter",
                status="prompt_ready",
                profile=settings.search_profile,
                prompt=prompt,
                metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
            )
            return {"status": "prompt_ready", "prompt": prompt}

        client = create_llm_client(settings)
        if client is None:
            return {"status": "disabled", "message": "HH_USE_LLM is false."}
        prompts = [
            prompt,
            build_cover_letter_prompt(vacancy_text=vacancy_text, resume_text=resume_text, strict=True),
        ]
        answer = None
        last_error: Exception | None = None
        for candidate_prompt in prompts:
            analytics.ai_assist_event(
                task_type="cover_letter",
                status="prompt_submitted",
                profile=settings.search_profile,
                prompt=candidate_prompt,
                metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
            )
            try:
                answer = client.ask_json(candidate_prompt, settings.llm_timeout_seconds)
            except LLMProviderError as exc:
                last_error = exc
                analytics.ai_assist_event(
                    task_type="cover_letter",
                    status="failed",
                    profile=settings.search_profile,
                    prompt=candidate_prompt,
                    error_reason=str(exc),
                    metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
                )
                raise
            try:
                _raise_on_validation_errors(validate_cover_letter_payload_shape(answer.parsed_json))
                last_error = None
                break
            except LLMProviderError as exc:
                last_error = exc
                analytics.ai_assist_event(
                    task_type="cover_letter",
                    status="failed",
                    profile=settings.search_profile,
                    prompt=candidate_prompt,
                    response_text=answer.raw_text,
                    parsed_json=answer.parsed_json,
                    error_reason=str(exc),
                    metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
                )
        if last_error is not None:
            raise last_error
        if answer is None:
            if last_error:
                raise last_error
            raise LLMProviderError("Unable to receive a valid cover letter from LLM providers.")

        analytics.ai_assist_event(
            task_type="cover_letter",
            status="answered",
            profile=settings.search_profile,
            prompt=candidate_prompt,
            response_text=answer.raw_text,
            parsed_json=answer.parsed_json,
            metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
        )
        return {
            "status": "answered",
            "provider": answer.provider,
            "model": answer.model,
            "parsed_json": answer.parsed_json,
            "raw_text": answer.raw_text,
        }
    except Exception as exc:
        analytics.ai_assist_event(
            task_type="cover_letter",
            status="failed",
            profile=settings.search_profile,
            prompt=prompt,
            error_reason=str(exc),
            metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
        )
        raise
    finally:
        analytics.close()


def llm_questionnaire(
    vacancy_text_path: Path,
    questions_json_path: Path,
    ask: bool = False,
) -> dict[str, object]:
    settings = Settings.load()
    settings.ensure_dirs()
    vacancy_text = vacancy_text_path.read_text(encoding="utf-8").strip()
    resume_text = load_resume_text(settings.resume_text_path)
    raw_questions = json.loads(questions_json_path.read_text(encoding="utf-8"))
    questions = [
        VacancyQuestion(
            question_id=str(question["question_id"]),
            label=str(question["label"]),
            input_type=str(question["input_type"]),
            options=[str(option) for option in question.get("options", [])],
            required=bool(question.get("required", True)),
        )
        for question in raw_questions
    ]
    prompt = build_questionnaire_prompt(vacancy_text, resume_text, questions)
    analytics = DuckDBAnalytics(settings.analytics_db_path, new_run_id())
    try:
        if not ask:
            analytics.ai_assist_event(
                task_type="questionnaire",
                status="prompt_ready",
                profile=settings.search_profile,
                prompt=prompt,
                metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
            )
            return {"status": "prompt_ready", "prompt": prompt}

        client = create_llm_client(settings)
        if client is None:
            return {"status": "disabled", "message": "HH_USE_LLM is false."}
        analytics.ai_assist_event(
            task_type="questionnaire",
            status="prompt_submitted",
            profile=settings.search_profile,
            prompt=prompt,
            metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
        )
        answer = client.ask_json(prompt, settings.llm_timeout_seconds)
        validation_errors = validate_questionnaire_payload_shape(answer.parsed_json)
        validation_errors.extend(_validate_questionnaire_answers(questions, answer.parsed_json))
        _raise_on_validation_errors(validation_errors)
        analytics.ai_assist_event(
            task_type="questionnaire",
            status="answered",
            profile=settings.search_profile,
            prompt=prompt,
            response_text=answer.raw_text,
            parsed_json=answer.parsed_json,
            metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
        )
        return {
            "status": "answered",
            "provider": answer.provider,
            "model": answer.model,
            "parsed_json": answer.parsed_json,
            "raw_text": answer.raw_text,
        }
    except Exception as exc:
        analytics.ai_assist_event(
            task_type="questionnaire",
            status="failed",
            profile=settings.search_profile,
            prompt=prompt,
            error_reason=str(exc),
            metadata={"vacancy_text": vacancy_text, "resume_text": resume_text},
        )
        raise
    finally:
        analytics.close()


def _validate_questionnaire_answers(
    questions: list[VacancyQuestion],
    payload: dict[str, object],
) -> list[str]:
    fields = [
        FormField(
            question=question,
            element=None,
            option_elements={option: None for option in question.options},
        )
        for question in questions
    ]
    return validate_structured_answers(fields, payload)


def _raise_on_validation_errors(errors: list[str]) -> None:
    if errors:
        raise LLMProviderError("; ".join(errors))
