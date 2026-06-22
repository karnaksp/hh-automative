"""SQLite-backed run state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hh_automative.models import Status, Vacancy


@dataclass(slots=True)
class VacancyRecord:
    vacancy_id: str
    url: str
    title: str
    company: str
    selected_resume: str
    status: Status
    error_reason: str
    processed_at: str


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def has_processed(self, vacancy_id: str, *, ignore_dry_run: bool = False) -> bool:
        if ignore_dry_run:
            row = self.connection.execute(
                """
                select 1
                from vacancies
                where vacancy_id = ? and status != ?
                limit 1
                """,
                (vacancy_id, Status.DRY_RUN.value),
            ).fetchone()
            return row is not None
        row = self.connection.execute(
            "select 1 from vacancies where vacancy_id = ? limit 1", (vacancy_id,)
        ).fetchone()
        return row is not None

    def record(
        self,
        vacancy: Vacancy,
        selected_resume: str,
        status: Status,
        error_reason: str = "",
        vacancy_text: str = "",
        resume_text: str = "",
    ) -> None:
        self.connection.execute(
            """
            insert into vacancies (
                vacancy_id, url, title, company, selected_resume, status, error_reason, processed_at,
                vacancy_text, resume_text
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(vacancy_id) do update set
                url = excluded.url,
                title = excluded.title,
                company = excluded.company,
                selected_resume = excluded.selected_resume,
                status = excluded.status,
                error_reason = excluded.error_reason,
                processed_at = excluded.processed_at,
                vacancy_text = excluded.vacancy_text,
                resume_text = excluded.resume_text
            """,
            (
                vacancy.vacancy_id,
                vacancy.url,
                vacancy.title,
                vacancy.company,
                selected_resume,
                status.value,
                error_reason,
                datetime.now(UTC).isoformat(),
                vacancy_text,
                resume_text,
            ),
        )
        self.connection.commit()

    def stats(self) -> dict[str, int]:
        rows = self.connection.execute(
            "select status, count(*) as count from vacancies group by status"
        ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def _init_schema(self) -> None:
        self.connection.execute(
            """
            create table if not exists vacancies (
                vacancy_id text primary key,
                url text not null,
                title text not null default '',
                company text not null default '',
                selected_resume text not null default '',
                status text not null,
                error_reason text not null default '',
                processed_at text not null,
                vacancy_text text not null default '',
                resume_text text not null default ''
            )
            """
        )
        self._ensure_column("vacancy_text", "vacancy_text text not null default ''")
        self._ensure_column("resume_text", "resume_text text not null default ''")
        self.connection.commit()

    def _ensure_column(self, name: str, definition: str) -> None:
        columns = [row[1] for row in self.connection.execute("pragma table_info(vacancies)").fetchall()]
        if name not in columns:
            self.connection.execute(f"alter table vacancies add column {definition}")
