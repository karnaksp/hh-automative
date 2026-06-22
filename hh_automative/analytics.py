"""DuckDB-backed analytical logging."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hh_automative.models import RunStats, Status, Vacancy

LOGGER = logging.getLogger(__name__)


def new_run_id() -> str:
    return str(uuid4())


def live_db_path_for(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.stem}_live.sqlite3")


class DuckDBAnalytics:
    def __init__(self, db_path: Path, run_id: str) -> None:
        import duckdb

        self.db_path = db_path
        self.live_db_path = live_db_path_for(db_path)
        self.run_id = run_id
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = None
        self.live_connection: sqlite3.Connection | None = None
        try:
            self.connection = duckdb.connect(str(self.db_path))
            self._init_schema()
        except duckdb.Error as exc:
            LOGGER.warning("DuckDB analytics disabled for %s: %s", self.db_path, exc)
        self._init_live_schema()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        if self.live_connection is not None:
            self.live_connection.close()

    def event(
        self,
        event_type: str,
        status: str = "",
        message: str = "",
        profile: str = "",
        vacancy: Vacancy | None = None,
        selected_resume: str = "",
        error_reason: str = "",
        diagnostics_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_ts = _now()
        values = [
            event_ts,
            self.run_id,
            event_type,
            status,
            profile,
            vacancy.vacancy_id if vacancy else "",
            vacancy.url if vacancy else "",
            vacancy.title if vacancy else "",
            vacancy.company if vacancy else "",
            selected_resume,
            message,
            error_reason,
            str(diagnostics_path) if diagnostics_path else "",
            _json(metadata or {}),
        ]
        if self.connection is not None:
            try:
                self.connection.execute(
                    """
                    insert into automation_events (
                        event_ts, run_id, event_type, status, profile, vacancy_id, vacancy_url,
                        title, company, selected_resume, message, error_reason, diagnostics_path,
                        metadata_json
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except Exception as exc:  # noqa: BLE001 - analytics must not break automation
                LOGGER.warning("Could not write automation event to DuckDB: %s", exc)
        self._write_live(
            """
            insert into automation_events (
                event_ts, run_id, event_type, status, profile, vacancy_id, vacancy_url,
                title, company, selected_resume, message, error_reason, diagnostics_path,
                metadata_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def run_started(self, profile: str, limit: int, dry_run: bool) -> None:
        self.event(
            "run_started",
            status="started",
            profile=profile,
            metadata={"limit": limit, "dry_run": dry_run},
        )

    def run_finished(self, profile: str, stats: RunStats) -> None:
        self.event(
            "run_finished",
            status="finished",
            profile=profile,
            metadata=stats.as_dict(),
        )

    def vacancy_result(
        self,
        profile: str,
        vacancy: Vacancy,
        status: Status,
        selected_resume: str = "",
        message: str = "",
        error_reason: str = "",
        diagnostics_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.event(
            "vacancy_result",
            status=status.value,
            profile=profile,
            vacancy=vacancy,
            selected_resume=selected_resume,
            message=message,
            error_reason=error_reason,
            diagnostics_path=diagnostics_path,
            metadata=metadata,
        )

    def ai_assist_event(
        self,
        task_type: str,
        status: str,
        profile: str = "",
        vacancy: Vacancy | None = None,
        prompt: str = "",
        response_text: str = "",
        parsed_json: dict[str, Any] | None = None,
        error_reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_ts = _now()
        values = [
            event_ts,
            self.run_id,
            task_type,
            status,
            profile,
            vacancy.vacancy_id if vacancy else "",
            vacancy.url if vacancy else "",
            prompt,
            response_text,
            _json(parsed_json or {}),
            error_reason,
            _json(metadata or {}),
        ]
        if self.connection is not None:
            try:
                self.connection.execute(
                    """
                    insert into ai_assist_events (
                        event_ts, run_id, task_type, status, profile, vacancy_id, vacancy_url,
                        prompt, response_text, parsed_json, error_reason, metadata_json
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except Exception as exc:  # noqa: BLE001 - analytics must not break automation
                LOGGER.warning("Could not write AI assist event to DuckDB: %s", exc)
        self._write_live(
            """
            insert into ai_assist_events (
                event_ts, run_id, task_type, status, profile, vacancy_id, vacancy_url,
                prompt, response_text, parsed_json, error_reason, metadata_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def _init_schema(self) -> None:
        if self.connection is None:
            return
        self.connection.execute(
            """
            create table if not exists automation_events (
                event_ts timestamp,
                run_id varchar,
                event_type varchar,
                status varchar,
                profile varchar,
                vacancy_id varchar,
                vacancy_url varchar,
                title varchar,
                company varchar,
                selected_resume varchar,
                message varchar,
                error_reason varchar,
                diagnostics_path varchar,
                metadata_json varchar
            )
            """
        )
        existing_automation_columns = {
            row[1]
            for row in self.connection.execute("pragma table_info(automation_events)").fetchall()
        }
        if "metadata_json" not in existing_automation_columns:
            self.connection.execute("alter table automation_events add column metadata_json varchar")
        self.connection.execute(
            """
            create table if not exists app_logs (
                log_ts timestamp,
                level varchar,
                logger varchar,
                message varchar,
                module varchar,
                function_name varchar,
                line_no integer,
                exception_text varchar
            )
            """
        )
        self.connection.execute(
            """
            create table if not exists ai_assist_events (
                event_ts timestamp,
                run_id varchar,
                task_type varchar,
                status varchar,
                profile varchar,
                vacancy_id varchar,
                vacancy_url varchar,
                prompt varchar,
                response_text varchar,
                parsed_json varchar,
                error_reason varchar,
                metadata_json varchar
            )
            """
        )
        existing_ai_columns = {
            row[1] for row in self.connection.execute("pragma table_info(ai_assist_events)").fetchall()
        }
        if "metadata_json" not in existing_ai_columns:
            self.connection.execute("alter table ai_assist_events add column metadata_json varchar")

    def _init_live_schema(self) -> None:
        try:
            self.live_connection = sqlite3.connect(self.live_db_path)
            self.live_connection.execute("pragma journal_mode=WAL")
            self.live_connection.execute(
                """
                create table if not exists automation_events (
                    event_ts text,
                    run_id text,
                    event_type text,
                    status text,
                    profile text,
                    vacancy_id text,
                    vacancy_url text,
                    title text,
                    company text,
                    selected_resume text,
                    message text,
                    error_reason text,
                    diagnostics_path text,
                    metadata_json text
                )
                """
            )
            self.live_connection.execute(
                """
                create table if not exists ai_assist_events (
                    event_ts text,
                    run_id text,
                    task_type text,
                    status text,
                    profile text,
                    vacancy_id text,
                    vacancy_url text,
                    prompt text,
                    response_text text,
                    parsed_json text,
                    error_reason text,
                    metadata_json text
                )
                """
            )
            self.live_connection.execute(
                """
                create table if not exists app_logs (
                    log_ts text,
                    level text,
                    logger text,
                    message text,
                    module text,
                    function_name text,
                    line_no integer,
                    exception_text text
                )
                """
            )
            self.live_connection.commit()
        except sqlite3.Error as exc:
            LOGGER.warning("Live SQLite analytics disabled for %s: %s", self.live_db_path, exc)
            self.live_connection = None

    def _write_live(self, sql: str, values: list[Any]) -> None:
        if self.live_connection is None:
            return
        normalized = [
            value.isoformat(sep=" ") if isinstance(value, datetime) else value for value in values
        ]
        try:
            self.live_connection.execute(sql, normalized)
            self.live_connection.commit()
        except sqlite3.Error as exc:
            LOGGER.warning("Could not write analytics event to live SQLite: %s", exc)


class DuckDBLogHandler(logging.Handler):
    def __init__(self, db_path: Path) -> None:
        super().__init__()
        import duckdb

        self.connection = None
        self.live_db_path = live_db_path_for(db_path)
        self.live_connection: sqlite3.Connection | None = None
        try:
            self.connection = duckdb.connect(str(db_path))
        except duckdb.Error:
            self.connection = None
        if self.connection is not None:
            self.connection.execute(
                """
                create table if not exists app_logs (
                    log_ts timestamp,
                    level varchar,
                    logger varchar,
                    message varchar,
                    module varchar,
                    function_name varchar,
                    line_no integer,
                    exception_text varchar
                )
                """
            )
        try:
            self.live_connection = sqlite3.connect(self.live_db_path)
            self.live_connection.execute("pragma journal_mode=WAL")
            self.live_connection.execute(
                """
                create table if not exists app_logs (
                    log_ts text,
                    level text,
                    logger text,
                    message text,
                    module text,
                    function_name text,
                    line_no integer,
                    exception_text text
                )
                """
            )
            self.live_connection.commit()
        except sqlite3.Error:
            self.live_connection = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.connection is None and self.live_connection is None:
            return
        try:
            exception_text = ""
            if record.exc_info:
                exception_text = self.formatException(record.exc_info)
            log_ts = datetime.fromtimestamp(record.created, UTC).replace(tzinfo=None)
            values = [
                log_ts,
                record.levelname,
                record.name,
                record.getMessage(),
                record.module,
                record.funcName,
                record.lineno,
                exception_text,
            ]
            if self.connection is not None:
                self.connection.execute(
                    """
                    insert into app_logs (
                        log_ts, level, logger, message, module, function_name, line_no,
                        exception_text
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            if self.live_connection is not None:
                self.live_connection.execute(
                    """
                    insert into app_logs (
                        log_ts, level, logger, message, module, function_name, line_no,
                        exception_text
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        log_ts.isoformat(sep=" "),
                        record.levelname,
                        record.name,
                        record.getMessage(),
                        record.module,
                        record.funcName,
                        record.lineno,
                        exception_text,
                    ],
                )
                self.live_connection.commit()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self.connection is not None:
                self.connection.close()
            if self.live_connection is not None:
                self.live_connection.close()
        finally:
            super().close()


def analytics_summary(db_path: Path) -> dict[str, Any]:
    import duckdb

    if not db_path.exists():
        return {"db_path": str(db_path), "runs": 0, "events": 0, "logs": 0}
    try:
        connection = duckdb.connect(str(db_path), read_only=True)
    except duckdb.Error as exc:
        return {
            "db_path": str(db_path),
            "status": "locked",
            "message": str(exc),
            "runs": 0,
            "events": 0,
            "ai_events": 0,
            "logs": 0,
            "vacancy_statuses": {},
        }
    try:
        events = _safe_count(connection, "automation_events")
        logs = _safe_count(connection, "app_logs")
        ai_events = _safe_count(connection, "ai_assist_events")
        statuses = []
        runs = 0
        if _table_exists(connection, "automation_events"):
            statuses = connection.execute(
                """
                select status, count(*) as count
                from automation_events
                where event_type = 'vacancy_result'
                group by status
                order by status
                """
            ).fetchall()
            runs = connection.execute(
                "select count(distinct run_id) from automation_events"
            ).fetchone()[0]
        return {
            "db_path": str(db_path),
            "runs": runs,
            "events": events,
            "ai_events": ai_events,
            "logs": logs,
            "vacancy_statuses": {status: count for status, count in statuses},
        }
    finally:
        connection.close()


def _safe_count(connection: Any, table: str) -> int:
    try:
        return connection.execute(f"select count(*) from {table}").fetchone()[0]
    except (sqlite3.Error, RuntimeError, Exception):
        return 0


def _table_exists(connection: Any, table: str) -> bool:
    return (
        connection.execute(
            """
            select count(*)
            from information_schema.tables
            where table_name = ?
            """,
            [table],
        ).fetchone()[0]
        > 0
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
