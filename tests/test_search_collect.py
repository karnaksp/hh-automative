from __future__ import annotations

from hh_automative.search import _vacancy_link_snapshots


class FakeDriver:
    def execute_script(self, script: str):  # noqa: ARG002
        return [
            {"href": "https://hh.ru/vacancy/1", "title": "Data Engineer"},
            {"href": None, "title": None},
            "bad item",
        ]


class FakeContext:
    driver = FakeDriver()


def test_vacancy_link_snapshots_normalizes_script_result() -> None:
    snapshots = _vacancy_link_snapshots(FakeContext())

    assert snapshots == [
        {"href": "https://hh.ru/vacancy/1", "title": "Data Engineer"},
        {"href": "", "title": ""},
    ]
