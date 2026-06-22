"""Data shaping helpers for the Streamlit admin dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from hh_automative.error_taxonomy import classify_error

_REVIEW_CATEGORIES = {
    "manual_review_required",
    "response_not_confirmed",
    "questionnaire_unknown_option",
    "llm_unsupported_claim",
}


@dataclass(slots=True)
class DashboardAlert:
    severity: str
    title: str
    detail: str


def discover_duckdb_paths(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        return []
    discovered = [path for path in data_dir.glob("*.duckdb") if path.is_file()]
    return sorted(
        discovered,
        key=lambda path: (
            0 if path.name == "hh_automative.duckdb" else 1,
            -path.stat().st_mtime,
            path.name,
        ),
    )


def prepare_events_frame(frame: pd.DataFrame, timestamp_column: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    prepared = frame.copy()
    if timestamp_column in prepared.columns:
        prepared[timestamp_column] = pd.to_datetime(
            prepared[timestamp_column], errors="coerce", utc=True
        ).dt.tz_convert(None)
    for column in (
        "event_type",
        "status",
        "profile",
        "vacancy_id",
        "vacancy_url",
        "title",
        "company",
        "selected_resume",
        "message",
        "error_reason",
        "diagnostics_path",
        "run_id",
        "task_type",
        "response_text",
        "parsed_json",
        "metadata_json",
        "logger",
        "level",
    ):
        if column in prepared.columns:
            prepared[column] = prepared[column].fillna("")
    return prepared


def with_review_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    prepared = frame.copy()
    for column in ("error_category", "severity", "recommended_action"):
        if column not in prepared.columns:
            prepared[column] = ""
    for index, row in prepared.iterrows():
        metadata = safe_json_loads(str(row.get("metadata_json", "")))
        info = classify_error(
            str(row.get("error_reason", "")),
            status=str(row.get("status", "")),
            message=str(row.get("message", "")),
        )
        prepared.at[index, "error_category"] = str(
            metadata.get("error_category") or info.category.value
        )
        prepared.at[index, "severity"] = str(metadata.get("severity") or info.severity)
        prepared.at[index, "recommended_action"] = str(
            metadata.get("recommended_action") or info.recommended_action
        )
    return prepared


def filter_by_timerange(
    frame: pd.DataFrame,
    timestamp_column: str,
    days: int | None,
) -> pd.DataFrame:
    if frame.empty or days is None or timestamp_column not in frame.columns:
        return frame.copy()
    latest = frame[timestamp_column].dropna().max()
    if pd.isna(latest):
        return frame.copy()
    threshold = latest - pd.Timedelta(days=days)
    return frame[frame[timestamp_column] >= threshold].copy()


def filter_dashboard_events(
    events: pd.DataFrame,
    *,
    profiles: list[str] | None = None,
    statuses: list[str] | None = None,
    search_text: str = "",
) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    filtered = events.copy()
    if profiles:
        filtered = filtered[filtered["profile"].isin(profiles)]
    if statuses:
        filtered = filtered[filtered["status"].isin(statuses)]
    normalized_search = search_text.strip().casefold()
    if normalized_search:
        haystack = (
            filtered["title"].astype(str)
            + " "
            + filtered["company"].astype(str)
            + " "
            + filtered["vacancy_url"].astype(str)
            + " "
            + filtered["error_reason"].astype(str)
            + " "
            + filtered["message"].astype(str)
        ).str.casefold()
        filtered = filtered[haystack.str.contains(normalized_search, na=False)]
    return filtered.copy()


def vacancy_results(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    if "event_type" not in events.columns:
        return pd.DataFrame()
    result = events[events["event_type"] == "vacancy_result"].copy()
    if "event_ts" in result.columns:
        result = result.sort_values("event_ts", ascending=False)
    return result


def build_overview_metrics(
    events: pd.DataFrame,
    vacancy_events: pd.DataFrame,
    ai_events: pd.DataFrame,
    logs: pd.DataFrame,
) -> dict[str, object]:
    latest_run_id = ""
    latest_run = pd.DataFrame()
    if not events.empty and "run_id" in events.columns:
        non_empty_run_ids = events[events["run_id"].astype(str).ne("")]
        if not non_empty_run_ids.empty:
            latest_run_id = str(non_empty_run_ids.sort_values("event_ts")["run_id"].iloc[-1])
            latest_run = non_empty_run_ids[non_empty_run_ids["run_id"] == latest_run_id].copy()

    latest_results = vacancy_results(latest_run)
    terminal_results = vacancy_events[vacancy_events["status"].isin(["success", "failure"])]
    ai_answered = ai_events[ai_events["status"] == "answered"] if not ai_events.empty else pd.DataFrame()
    ai_failed = ai_events[ai_events["status"] == "failed"] if not ai_events.empty else pd.DataFrame()

    success_rate = 0.0
    if not terminal_results.empty:
        success_rate = (
            len(terminal_results[terminal_results["status"] == "success"]) / len(terminal_results)
        ) * 100

    latest_run_success_rate = 0.0
    latest_terminal = latest_results[latest_results["status"].isin(["success", "failure"])]
    if not latest_terminal.empty:
        latest_run_success_rate = (
            len(latest_terminal[latest_terminal["status"] == "success"]) / len(latest_terminal)
        ) * 100

    return {
        "runs": events["run_id"].nunique() if not events.empty else 0,
        "vacancies": len(vacancy_events),
        "successes": int((vacancy_events["status"] == "success").sum()) if not vacancy_events.empty else 0,
        "failures": int((vacancy_events["status"] == "failure").sum()) if not vacancy_events.empty else 0,
        "success_rate": success_rate,
        "latest_run_id": latest_run_id,
        "latest_run_vacancies": len(latest_results),
        "latest_run_success_rate": latest_run_success_rate,
        "ai_answered": len(ai_answered),
        "ai_failed": len(ai_failed),
        "warnings": int((logs["level"] == "WARNING").sum()) if not logs.empty else 0,
        "errors": int((logs["level"] == "ERROR").sum()) if not logs.empty else 0,
    }


def build_run_summary(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    grouped = (
        events.groupby("run_id", dropna=False)
        .agg(
            started_at=("event_ts", "min"),
            finished_at=("event_ts", "max"),
            profile=("profile", "last"),
            total_events=("event_type", "count"),
        )
        .reset_index()
    )
    results = vacancy_results(events)
    if not results.empty:
        status_counts = (
            results.pivot_table(
                index="run_id",
                columns="status",
                values="vacancy_id",
                aggfunc="count",
                fill_value=0,
            )
            .reset_index()
        )
        grouped = grouped.merge(status_counts, on="run_id", how="left")
    for column in ("success", "failure", "skipped", "dry_run"):
        if column not in grouped.columns:
            grouped[column] = 0
        grouped[column] = grouped[column].fillna(0).astype(int)
    grouped["vacancies"] = grouped[["success", "failure", "skipped", "dry_run"]].sum(axis=1)
    grouped["success_rate"] = (
        grouped["success"] / (grouped["success"] + grouped["failure"]).replace(0, pd.NA)
    ).fillna(0) * 100
    grouped["duration_minutes"] = (
        (grouped["finished_at"] - grouped["started_at"]).dt.total_seconds() / 60
    ).round(1)
    return grouped.sort_values("started_at", ascending=False)


def build_status_timeline(vacancy_events: pd.DataFrame) -> pd.DataFrame:
    if vacancy_events.empty:
        return pd.DataFrame()
    timeline = vacancy_events.copy()
    timeline["day"] = timeline["event_ts"].dt.date
    pivot = (
        timeline.groupby(["day", "status"])
        .size()
        .reset_index(name="count")
        .pivot(index="day", columns="status", values="count")
        .fillna(0)
        .sort_index()
    )
    pivot.index = pivot.index.astype(str)
    return pivot


def build_profile_matrix(vacancy_events: pd.DataFrame) -> pd.DataFrame:
    if vacancy_events.empty:
        return pd.DataFrame()
    matrix = (
        vacancy_events.groupby(["profile", "status"])
        .size()
        .reset_index(name="count")
        .pivot(index="profile", columns="status", values="count")
        .fillna(0)
        .astype(int)
    )
    return matrix.reset_index()


def build_top_error_table(vacancy_events: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    if vacancy_events.empty:
        return pd.DataFrame()
    failing = with_review_columns(vacancy_events)
    failing = failing[failing["error_reason"].astype(str).ne("")].copy()
    if failing.empty:
        return pd.DataFrame()
    top = (
        failing.groupby("error_reason")
        .agg(
            count=("vacancy_id", "count"),
            last_seen=("event_ts", "max"),
            example_title=("title", "first"),
            example_url=("vacancy_url", "first"),
            category=("error_category", "first"),
            severity=("severity", "first"),
            recommended_action=("recommended_action", "first"),
        )
        .reset_index()
        .sort_values(["count", "last_seen"], ascending=[False, False])
        .head(limit)
    )
    return top


def build_problem_vacancies(vacancy_events: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    if vacancy_events.empty:
        return pd.DataFrame()
    enriched = with_review_columns(vacancy_events)
    problems = enriched[
        enriched["status"].isin(["failure"])
        | enriched["error_reason"].astype(str).ne("")
        | enriched["diagnostics_path"].astype(str).ne("")
    ].copy()
    if problems.empty:
        return pd.DataFrame()
    columns = [
        "event_ts",
        "profile",
        "status",
        "title",
        "company",
        "vacancy_url",
        "error_reason",
        "error_category",
        "severity",
        "recommended_action",
        "diagnostics_path",
        "selected_resume",
    ]
    existing = [column for column in columns if column in problems.columns]
    return problems[existing].sort_values("event_ts", ascending=False).head(limit)


def build_manual_review_vacancies(
    vacancy_events: pd.DataFrame,
    ai_events: pd.DataFrame | None = None,
    limit: int = 100,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if not vacancy_events.empty:
        enriched_vacancies = with_review_columns(vacancy_events)
        review_vacancies = enriched_vacancies[
            enriched_vacancies["error_category"].isin(_REVIEW_CATEGORIES)
            | enriched_vacancies["status"].isin(["failure"])
        ].copy()
        if not review_vacancies.empty:
            review_vacancies["source"] = "vacancy"
            review_vacancies["prompt"] = ""
            review_vacancies["response_text"] = ""
            review_vacancies["parsed_json"] = ""
            frames.append(review_vacancies)

    if ai_events is not None and not ai_events.empty:
        enriched_ai = with_review_columns(ai_events)
        review_ai = enriched_ai[
            enriched_ai["error_category"].isin(_REVIEW_CATEGORIES)
            | enriched_ai["status"].isin(["failed"])
        ].copy()
        if not review_ai.empty:
            review_ai["source"] = "ai:" + review_ai["task_type"].astype(str)
            for column in ("title", "company", "selected_resume", "message", "diagnostics_path"):
                if column not in review_ai.columns:
                    review_ai[column] = ""
            frames.append(review_ai)

    if not frames:
        return pd.DataFrame()

    review = pd.concat(frames, ignore_index=True, sort=False)
    columns = [
        "event_ts",
        "source",
        "severity",
        "error_category",
        "recommended_action",
        "profile",
        "title",
        "company",
        "vacancy_url",
        "error_reason",
        "diagnostics_path",
        "selected_resume",
        "message",
        "prompt",
        "response_text",
        "parsed_json",
    ]
    existing = [column for column in columns if column in review.columns]
    return review[existing].sort_values("event_ts", ascending=False).head(limit)


def build_ai_status_table(ai_events: pd.DataFrame) -> pd.DataFrame:
    if ai_events.empty:
        return pd.DataFrame()
    return (
        ai_events.groupby(["task_type", "status"])
        .size()
        .reset_index(name="count")
        .sort_values(["task_type", "count"], ascending=[True, False])
    )


def build_recent_ai_table(ai_events: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    if ai_events.empty:
        return pd.DataFrame()
    recent = ai_events.copy().sort_values("event_ts", ascending=False).head(limit)
    recent["response_preview"] = recent["response_text"].astype(str).str.replace("\n", " ").str[:180]
    return recent[
        [
            "event_ts",
            "task_type",
            "status",
            "profile",
            "vacancy_url",
            "response_preview",
            "error_reason",
        ]
    ]


def build_latest_cover_letters(ai_events: pd.DataFrame, limit: int = 5) -> pd.DataFrame:
    if ai_events.empty:
        return pd.DataFrame()
    answered = ai_events[
        (ai_events["task_type"] == "cover_letter") & (ai_events["status"] == "answered")
    ].copy()
    if answered.empty:
        return pd.DataFrame()
    answered["cover_letter"] = answered["parsed_json"].astype(str).map(extract_cover_letter_text)
    answered["cover_letter_length"] = answered["cover_letter"].str.len()
    return answered[
        ["event_ts", "profile", "vacancy_url", "cover_letter", "cover_letter_length", "metadata_json"]
    ].sort_values("event_ts", ascending=False).head(limit)


def build_live_activity(
    vacancy_events: pd.DataFrame,
    ai_events: pd.DataFrame,
    logs: pd.DataFrame,
    limit: int = 50,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    if not vacancy_events.empty:
        vacancy_feed = vacancy_events.copy()
        vacancy_feed["timestamp"] = vacancy_feed["event_ts"]
        vacancy_feed["source"] = "vacancy"
        vacancy_feed["level"] = vacancy_feed["status"].map(
            {
                "success": "INFO",
                "failure": "ERROR",
                "skipped": "INFO",
                "dry_run": "INFO",
            }
        ).fillna("INFO")
        vacancy_feed["summary"] = vacancy_feed.apply(
            lambda row: _join_non_empty(
                [
                    str(row.get("status", "")).upper(),
                    str(row.get("title", "")),
                    str(row.get("error_reason", "")) or str(row.get("message", "")),
                ]
            ),
            axis=1,
        )
        vacancy_feed["details"] = vacancy_feed["message"].astype(str)
        frames.append(vacancy_feed[["timestamp", "source", "level", "summary", "vacancy_url", "details"]])

    if not ai_events.empty:
        ai_feed = ai_events.copy()
        ai_feed["timestamp"] = ai_feed["event_ts"]
        ai_feed["source"] = ai_feed["task_type"].astype(str).map(lambda task: f"ai:{task}")
        ai_feed["level"] = ai_feed["status"].map(
            {
                "answered": "INFO",
                "inserted": "INFO",
                "prompt_submitted": "INFO",
                "skipped": "WARNING",
                "not_available": "WARNING",
                "failed": "ERROR",
            }
        ).fillna("INFO")
        ai_feed["summary"] = ai_feed.apply(
            lambda row: _join_non_empty(
                [
                    str(row.get("status", "")),
                    str(row.get("error_reason", "")),
                ]
            ),
            axis=1,
        )
        ai_feed["details"] = ai_feed["response_text"].astype(str).str.replace("\n", " ").str[:240]
        frames.append(ai_feed[["timestamp", "source", "level", "summary", "vacancy_url", "details"]])

    if not logs.empty:
        log_feed = logs.copy()
        log_feed["timestamp"] = log_feed["log_ts"]
        log_feed["source"] = log_feed["logger"].astype(str)
        log_feed["summary"] = log_feed["message"].astype(str)
        log_feed["vacancy_url"] = ""
        log_feed["details"] = ""
        frames.append(log_feed[["timestamp", "source", "level", "summary", "vacancy_url", "details"]])

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["vacancy_url"] = combined["vacancy_url"].fillna("")
    combined["details"] = combined["details"].fillna("")
    return combined.sort_values("timestamp", ascending=False).head(limit)


def extract_cover_letter_text(parsed_json_value: str) -> str:
    parsed = safe_json_loads(parsed_json_value)
    if not parsed:
        return ""
    return str(parsed.get("cover_letter", "")).strip()


def safe_json_loads(value: str) -> dict[str, object]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _join_non_empty(parts: list[str]) -> str:
    return " | ".join(part.strip() for part in parts if part and part.strip())


def build_alerts(
    vacancy_events: pd.DataFrame,
    ai_events: pd.DataFrame,
    logs: pd.DataFrame,
) -> list[DashboardAlert]:
    alerts: list[DashboardAlert] = []
    if vacancy_events.empty:
        return [
            DashboardAlert(
                severity="info",
                title="Нет данных по откликам",
                detail="В выбранной базе нет vacancy_result событий. Сначала нужен хотя бы один run.",
            )
        ]

    latest_run_id = str(vacancy_events["run_id"].iloc[0])
    latest_run = vacancy_events[vacancy_events["run_id"] == latest_run_id].copy()
    latest_failures = int((latest_run["status"] == "failure").sum())
    latest_successes = int((latest_run["status"] == "success").sum())
    if latest_failures > 0:
        alerts.append(
            DashboardAlert(
                severity="critical",
                title="В последнем запуске были сбои",
                detail=(
                    f"Последний run дал {latest_failures} failure и {latest_successes} success. "
                    "Смотри Problem Vacancies и Top blockers."
                ),
            )
        )

    terminal = vacancy_events[vacancy_events["status"].isin(["success", "failure"])]
    if not terminal.empty:
        failure_rate = (terminal["status"] == "failure").mean()
        if failure_rate >= 0.25:
            alerts.append(
                DashboardAlert(
                    severity="warning",
                    title="Высокая доля failures",
                    detail=f"Failure rate по terminal результатам сейчас {failure_rate:.0%}.",
                )
            )

    error_table = build_top_error_table(vacancy_events, limit=1)
    if not error_table.empty and int(error_table.iloc[0]["count"]) >= 3:
        alerts.append(
            DashboardAlert(
                severity="warning",
                title="Повторяющийся blocker",
                detail=(
                    f"Чаще всего повторяется ошибка: {error_table.iloc[0]['error_reason']} "
                    f"(x{int(error_table.iloc[0]['count'])})."
                ),
            )
        )

    if not ai_events.empty:
        failed_ai = int((ai_events["status"] == "failed").sum())
        answered_ai = int((ai_events["status"] == "answered").sum())
        if failed_ai > 0:
            alerts.append(
                DashboardAlert(
                    severity="warning",
                    title="LLM path нестабилен",
                    detail=f"В AI assist есть {failed_ai} failed событий при {answered_ai} answered.",
                )
            )
        elif answered_ai > 0:
            alerts.append(
                DashboardAlert(
                    severity="success",
                    title="LLM path подтвержден",
                    detail=f"В AI assist есть {answered_ai} answered событий без failed в текущем срезе.",
                )
            )

    if not logs.empty:
        recent_errors = int(logs["level"].isin(["ERROR", "WARNING"]).sum())
        if recent_errors > 0:
            alerts.append(
                DashboardAlert(
                    severity="info",
                    title="Есть warning/error логи",
                    detail=f"В текущем срезе найдено {recent_errors} warning/error записей.",
                )
            )

    return alerts
