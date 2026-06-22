from __future__ import annotations

from hh_automative.analytics import DuckDBAnalytics, analytics_summary
from hh_automative.models import Status, Vacancy


def test_duckdb_analytics_records_vacancy_result(tmp_path) -> None:
    db_path = tmp_path / "analytics.duckdb"
    analytics = DuckDBAnalytics(db_path, "run-1")
    vacancy = Vacancy(
        url="https://hh.ru/vacancy/123",
        vacancy_id="123",
        title="Data Engineer",
        company="Company",
    )

    try:
        analytics.run_started("data-engineer", limit=1, dry_run=True)
        analytics.vacancy_result(
            "data-engineer",
            vacancy,
            Status.DRY_RUN,
            selected_resume="Data Scientist",
            message="Dry run",
        )
    finally:
        analytics.close()

    summary = analytics_summary(db_path)

    assert summary["runs"] == 1
    assert summary["events"] == 2
    assert summary["vacancy_statuses"] == {"dry_run": 1}
