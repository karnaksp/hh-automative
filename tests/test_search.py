from __future__ import annotations

from hh_automative.search import _next_pager_page_href


def test_next_pager_page_href_uses_next_numeric_page() -> None:
    href = _next_pager_page_href(
        "https://hh.ru/search/vacancy?text=data+engineer",
        [
            "https://hh.ru/search/vacancy?text=data+engineer&page=0",
            "https://hh.ru/search/vacancy?text=data+engineer&page=1",
            "https://hh.ru/search/vacancy?text=data+engineer&page=2",
        ],
    )

    assert href.endswith("page=1")


def test_next_pager_page_href_advances_from_current_page() -> None:
    href = _next_pager_page_href(
        "https://hh.ru/search/vacancy?text=data+engineer&page=1",
        [
            "https://hh.ru/search/vacancy?text=data+engineer&page=0",
            "https://hh.ru/search/vacancy?text=data+engineer&page=1",
            "https://hh.ru/search/vacancy?text=data+engineer&page=2",
        ],
    )

    assert href.endswith("page=2")
