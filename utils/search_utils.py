"""Backward-compatible search helpers."""

from __future__ import annotations

from hh_automative.search import collect_vacancies, open_search


def clear_region(_driver) -> None:
    return None


def select_all_countries(_driver) -> None:
    return None


def international_ok(driver) -> None:
    driver.refresh()


__all__ = ["clear_region", "collect_vacancies", "international_ok", "open_search", "select_all_countries"]
