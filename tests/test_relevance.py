from __future__ import annotations

from hh_automative.relevance import is_relevant_data_engineer_title


def test_data_engineer_titles_pass_post_filter() -> None:
    assert is_relevant_data_engineer_title("Senior Data Engineer")
    assert is_relevant_data_engineer_title("Инженер данных / DWH")
    assert is_relevant_data_engineer_title("ETL Developer")


def test_non_data_engineer_titles_are_skipped() -> None:
    assert not is_relevant_data_engineer_title("DevOps Engineer")
    assert not is_relevant_data_engineer_title("SRE Observability Engineer")
    assert not is_relevant_data_engineer_title("Operations Project Manager")
    assert not is_relevant_data_engineer_title("Data Analyst")
