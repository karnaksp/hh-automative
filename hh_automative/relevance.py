"""Vacancy relevance checks for safe Data Engineer automation."""

from __future__ import annotations

import re

_POSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bdata\s+engineer\b",
        r"\bdata\s+platform\b",
        r"\bdwh\b",
        r"\betl\b",
        r"\belt\b",
        r"\bbig\s+data\b",
        r"дата[-\s]?инженер",
        r"инженер\s+данных",
        r"платформ[а-я\s]+данн",
        r"хранилищ[а-я\s]+данн",
    )
)

_NEGATIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bdevops\b",
        r"\bsre\b",
        r"\bsite\s+reliability\b",
        r"\bobservability\b",
        r"\bplatform\s+engineer\b",
        r"\binfrastructure\s+engineer\b",
        r"\boperations?\s+project\s+manager\b",
        r"\bproject\s+manager\b",
        r"\bproduct\s+manager\b",
        r"\bdata\s+analyst\b",
        r"\bdata\s+scientist\b",
        r"\bml\s+engineer\b",
        r"дата[-\s]?аналитик",
        r"аналитик\s+данных",
        r"data\s+science",
    )
)


def is_relevant_data_engineer_title(title: str) -> bool:
    """Return true when a title is safe to process for a Data Engineer profile."""
    normalized = " ".join((title or "").split())
    if not normalized:
        return True
    if any(pattern.search(normalized) for pattern in _NEGATIVE_PATTERNS):
        return False
    return any(pattern.search(normalized) for pattern in _POSITIVE_PATTERNS)
