"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Status(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass(slots=True)
class ActionResult:
    status: Status
    message: str = ""
    error_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {Status.SUCCESS, Status.DRY_RUN, Status.SKIPPED}


@dataclass(slots=True)
class Vacancy:
    url: str
    vacancy_id: str
    title: str = ""
    company: str = ""


@dataclass(slots=True)
class ResumeProfile:
    default_resume: str
    resume_codes: dict[str, str]

    def choose_resume_code(self, vacancy_title: str) -> tuple[str, str]:
        normalized_title = vacancy_title.lower()
        selected_name = self.default_resume
        selected_code = self.resume_codes[self.default_resume]
        for resume_name, resume_code in self.resume_codes.items():
            if resume_name.lower() in normalized_title:
                return resume_name, resume_code
        return selected_name, selected_code


@dataclass(slots=True)
class SearchProfile:
    name: str
    query: str
    exclude: str = ""
    region: str = "global"
    min_salary: str = ""
    only_with_salary: bool = False
    advanced_search_url: str = ""


@dataclass(slots=True)
class RunStats:
    scanned: int = 0
    sent: int = 0
    dry_run: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def record(self, status: Status) -> None:
        if status == Status.SUCCESS:
            self.sent += 1
        elif status == Status.DRY_RUN:
            self.dry_run += 1
        elif status == Status.SKIPPED:
            self.skipped += 1
        else:
            self.failed += 1

    def as_dict(self) -> dict[str, int | str]:
        return {
            "scanned": self.scanned,
            "sent": self.sent,
            "dry_run": self.dry_run,
            "skipped": self.skipped,
            "failed": self.failed,
            "started_at": self.started_at.isoformat(),
        }
