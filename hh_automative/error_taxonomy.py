"""Normalized error categories for automation review and dashboarding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    FORM_SUGGESTION_DETECTED = "form_suggestion_detected"
    LLM_UNSUPPORTED_CLAIM = "llm_unsupported_claim"
    QUESTIONNAIRE_UNKNOWN_OPTION = "questionnaire_unknown_option"
    RESPONSE_NOT_CONFIRMED = "response_not_confirmed"
    IRRELEVANT_VACANCY = "irrelevant_vacancy"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    category: ErrorCategory
    severity: str
    recommended_action: str


_NO_ACTION = "No manual action required."


def classify_error(
    error_reason: str = "",
    *,
    status: str = "",
    message: str = "",
) -> ErrorInfo:
    text = " ".join(part for part in (error_reason, status, message) if part).casefold()
    if not text:
        return ErrorInfo(ErrorCategory.UNKNOWN, "info", _NO_ACTION)

    if "irrelevant_title" in text or "irrelevant vacancy title" in text:
        return ErrorInfo(
            ErrorCategory.IRRELEVANT_VACANCY,
            "info",
            "No response needed; title was filtered out by Data Engineer relevance rules.",
        )
    if "unsupported numeric" in text or "unsupported number" in text:
        return ErrorInfo(
            ErrorCategory.LLM_UNSUPPORTED_CLAIM,
            "warning",
            "Review rejected LLM text; improve prompt or resume grounding before reuse.",
        )
    if "selected unknown option" in text or "unknown option" in text:
        return ErrorInfo(
            ErrorCategory.QUESTIONNAIRE_UNKNOWN_OPTION,
            "critical",
            "Open the vacancy manually or update questionnaire option extraction.",
        )
    if "vacancy-response-suggest" in text or "suggestion" in text:
        return ErrorInfo(
            ErrorCategory.FORM_SUGGESTION_DETECTED,
            "warning",
            "Check whether hh.ru suggestion widgets were incorrectly treated as questions.",
        )
    if "did not show a confirmed response success state" in text or "response_not_confirmed" in text:
        return ErrorInfo(
            ErrorCategory.RESPONSE_NOT_CONFIRMED,
            "critical",
            "Open the vacancy on hh.ru and verify whether the response was actually sent.",
        )
    if (
        "manual review" in text
        or "needs_human_review" in text
        or "requires additional answers" in text
        or "additional answers after submit" in text
    ):
        return ErrorInfo(
            ErrorCategory.MANUAL_REVIEW_REQUIRED,
            "critical",
            "Handle this vacancy manually before enabling automatic submission.",
        )
    if status == "failure" or error_reason:
        return ErrorInfo(
            ErrorCategory.MANUAL_REVIEW_REQUIRED,
            "warning",
            "Review raw error and diagnostics before retrying.",
        )
    return ErrorInfo(ErrorCategory.UNKNOWN, "info", _NO_ACTION)


def review_metadata(
    error_reason: str = "",
    *,
    status: str = "",
    message: str = "",
) -> dict[str, str]:
    info = classify_error(error_reason, status=status, message=message)
    return {
        "error_category": info.category.value,
        "severity": info.severity,
        "recommended_action": info.recommended_action,
    }
