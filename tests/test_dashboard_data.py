from __future__ import annotations

import pandas as pd

from hh_automative.dashboard_data import (
    build_alerts,
    build_live_activity,
    build_manual_review_vacancies,
    build_overview_metrics,
    build_top_error_table,
    extract_cover_letter_text,
    vacancy_results,
)


def test_vacancy_results_filters_only_result_events() -> None:
    events = pd.DataFrame(
        [
            {"event_type": "run_started", "status": "started", "event_ts": pd.Timestamp("2026-06-18T10:00:00")},
            {"event_type": "vacancy_result", "status": "success", "event_ts": pd.Timestamp("2026-06-18T10:01:00")},
            {"event_type": "vacancy_result", "status": "failure", "event_ts": pd.Timestamp("2026-06-18T10:02:00")},
        ]
    )

    result = vacancy_results(events)

    assert len(result) == 2
    assert result.iloc[0]["status"] == "failure"


def test_build_top_error_table_summarizes_repeated_failures() -> None:
    vacancy_events = pd.DataFrame(
        [
            {
                "event_ts": pd.Timestamp("2026-06-18T10:00:00"),
                "vacancy_id": "1",
                "title": "A",
                "vacancy_url": "https://hh.ru/vacancy/1",
                "error_reason": "Response button was not found.",
            },
            {
                "event_ts": pd.Timestamp("2026-06-18T11:00:00"),
                "vacancy_id": "2",
                "title": "B",
                "vacancy_url": "https://hh.ru/vacancy/2",
                "error_reason": "Response button was not found.",
            },
        ]
    )

    top = build_top_error_table(vacancy_events)

    assert len(top) == 1
    assert top.iloc[0]["count"] == 2
    assert top.iloc[0]["error_reason"] == "Response button was not found."


def test_build_alerts_highlights_failures_and_ai_instability() -> None:
    vacancy_events = pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "event_ts": pd.Timestamp("2026-06-18T12:00:00"),
                "status": "failure",
                "vacancy_id": "1",
                "title": "A",
                "vacancy_url": "https://hh.ru/vacancy/1",
                "error_reason": "Vacancy requires additional answers.",
                "diagnostics_path": "reports/1.png",
            },
            {
                "run_id": "run-1",
                "event_ts": pd.Timestamp("2026-06-18T12:01:00"),
                "status": "failure",
                "vacancy_id": "2",
                "title": "B",
                "vacancy_url": "https://hh.ru/vacancy/2",
                "error_reason": "Vacancy requires additional answers.",
                "diagnostics_path": "reports/2.png",
            },
            {
                "run_id": "run-1",
                "event_ts": pd.Timestamp("2026-06-18T12:02:00"),
                "status": "success",
                "vacancy_id": "3",
                "title": "C",
                "vacancy_url": "https://hh.ru/vacancy/3",
                "error_reason": "",
                "diagnostics_path": "",
            },
        ]
    )
    ai_events = pd.DataFrame(
        [
            {"status": "failed", "task_type": "cover_letter"},
            {"status": "answered", "task_type": "cover_letter"},
        ]
    )
    logs = pd.DataFrame([{"level": "WARNING"}])

    alerts = build_alerts(vacancy_events, ai_events, logs)
    titles = {alert.title for alert in alerts}

    assert "В последнем запуске были сбои" in titles
    assert "LLM path нестабилен" in titles
    assert "Повторяющийся blocker" not in titles


def test_build_overview_metrics_counts_success_rate() -> None:
    events = pd.DataFrame(
        [
            {"run_id": "run-1", "event_ts": pd.Timestamp("2026-06-18T10:00:00"), "event_type": "run_started", "status": "started"},
            {"run_id": "run-1", "event_ts": pd.Timestamp("2026-06-18T10:05:00"), "event_type": "vacancy_result", "status": "success"},
            {"run_id": "run-1", "event_ts": pd.Timestamp("2026-06-18T10:06:00"), "event_type": "vacancy_result", "status": "failure"},
        ]
    )
    vacancy_events = vacancy_results(events)
    metrics = build_overview_metrics(events, vacancy_events, pd.DataFrame(), pd.DataFrame())

    assert metrics["runs"] == 1
    assert metrics["vacancies"] == 2
    assert metrics["success_rate"] == 50.0


def test_extract_cover_letter_text_reads_json_payload() -> None:
    text = extract_cover_letter_text('{"cover_letter": "Готовый текст"}')

    assert text == "Готовый текст"


def test_build_live_activity_merges_vacancy_ai_and_logs() -> None:
    vacancy_events = pd.DataFrame(
        [
            {
                "event_ts": pd.Timestamp("2026-06-18T10:02:00"),
                "status": "success",
                "title": "Data Engineer",
                "vacancy_url": "https://hh.ru/vacancy/1",
                "message": "Response submitted.",
                "error_reason": "",
            }
        ]
    )
    ai_events = pd.DataFrame(
        [
            {
                "event_ts": pd.Timestamp("2026-06-18T10:03:00"),
                "task_type": "cover_letter",
                "status": "inserted",
                "vacancy_url": "https://hh.ru/vacancy/1",
                "response_text": "",
                "error_reason": "",
            }
        ]
    )
    logs = pd.DataFrame(
        [
            {
                "log_ts": pd.Timestamp("2026-06-18T10:04:00"),
                "level": "WARNING",
                "logger": "hh_automative.response",
                "message": "Submit click failed, trying form submit fallback.",
            }
        ]
    )

    feed = build_live_activity(vacancy_events, ai_events, logs, limit=10)

    assert len(feed) == 3
    assert list(feed["source"]) == ["hh_automative.response", "ai:cover_letter", "vacancy"]


def test_build_manual_review_vacancies_keeps_review_categories() -> None:
    vacancy_events = pd.DataFrame(
        [
            {
                "event_ts": pd.Timestamp("2026-06-18T10:00:00"),
                "status": "success",
                "profile": "data-engineer",
                "title": "OK",
                "company": "A",
                "vacancy_url": "https://hh.ru/vacancy/1",
                "error_reason": "",
                "diagnostics_path": "",
                "selected_resume": "Data engineer",
                "message": "Response submitted.",
                "metadata_json": "",
            },
            {
                "event_ts": pd.Timestamp("2026-06-18T11:00:00"),
                "status": "failure",
                "profile": "data-engineer",
                "title": "Broken",
                "company": "B",
                "vacancy_url": "https://hh.ru/vacancy/2",
                "error_reason": "Submit was triggered, but hh.ru did not show a confirmed response success state.",
                "diagnostics_path": "reports/diag.html",
                "selected_resume": "Data engineer",
                "message": "",
                "metadata_json": (
                    '{"error_category": "response_not_confirmed", '
                    '"severity": "critical", '
                    '"recommended_action": "Verify manually"}'
                ),
            },
        ]
    )

    review = build_manual_review_vacancies(vacancy_events)

    assert len(review) == 1
    assert review.iloc[0]["title"] == "Broken"
    assert review.iloc[0]["vacancy_url"] == "https://hh.ru/vacancy/2"
    assert review.iloc[0]["error_category"] == "response_not_confirmed"
    assert review.iloc[0]["recommended_action"] == "Verify manually"


def test_build_manual_review_vacancies_includes_rejected_llm_letters() -> None:
    ai_events = pd.DataFrame(
        [
            {
                "event_ts": pd.Timestamp("2026-06-18T12:00:00"),
                "task_type": "cover_letter",
                "status": "failed",
                "profile": "data-engineer",
                "vacancy_url": "https://hh.ru/vacancy/3",
                "error_reason": "unsupported numeric claims: 100 TB",
                "metadata_json": "",
                "prompt": "prompt",
                "response_text": "bad response",
                "parsed_json": '{"cover_letter": "bad"}',
            }
        ]
    )

    review = build_manual_review_vacancies(pd.DataFrame(), ai_events)

    assert len(review) == 1
    assert review.iloc[0]["source"] == "ai:cover_letter"
    assert review.iloc[0]["error_category"] == "llm_unsupported_claim"
    assert review.iloc[0]["prompt"] == "prompt"
