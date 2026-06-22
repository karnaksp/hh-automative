from __future__ import annotations

from hh_automative.error_taxonomy import ErrorCategory, classify_error, review_metadata


def test_classify_unknown_questionnaire_option() -> None:
    info = classify_error("question 'SQL' selected unknown option 'Иногда'")

    assert info.category == ErrorCategory.QUESTIONNAIRE_UNKNOWN_OPTION
    assert info.severity == "critical"


def test_classify_unsupported_numeric_claim() -> None:
    info = classify_error("unsupported numeric claims: 100 TB")

    assert info.category == ErrorCategory.LLM_UNSUPPORTED_CLAIM
    assert info.severity == "warning"


def test_review_metadata_serializable_values() -> None:
    metadata = review_metadata("irrelevant_title", status="skipped")

    assert metadata["error_category"] == "irrelevant_vacancy"
    assert metadata["severity"] == "info"
