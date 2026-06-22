from __future__ import annotations

from hh_automative.models import Status, Vacancy
from hh_automative.state import StateStore


def test_state_store_records_and_skips_processed_vacancy(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    vacancy = Vacancy(
        url="https://hh.ru/vacancy/123",
        vacancy_id="123",
        title="Data Scientist",
        company="Company",
    )

    try:
        assert not store.has_processed("123")
        store.record(vacancy, "Data Scientist", Status.DRY_RUN)

        assert store.has_processed("123")
        assert not store.has_processed("123", ignore_dry_run=True)
        assert store.stats() == {"dry_run": 1}

        store.record(vacancy, "Data Scientist", Status.SUCCESS)
        assert store.has_processed("123", ignore_dry_run=True)
    finally:
        store.close()
