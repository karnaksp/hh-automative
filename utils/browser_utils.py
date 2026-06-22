"""Backward-compatible wrappers around hh_automative browser helpers."""

from __future__ import annotations

import json
from pathlib import Path

from hh_automative.auth import login_or_restore_session
from hh_automative.browser import BotContext, click, scroll_to_bottom, wait_for
from hh_automative.search import open_search
from hh_automative.settings import Settings, load_search_profile


def load_data_from_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_data_to_json(data: object, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_cookies_and_login(driver, *_args) -> None:
    from selenium.webdriver.support.ui import WebDriverWait

    settings = Settings.load()
    context = BotContext(
        driver=driver,
        wait=WebDriverWait(driver, settings.timeout_seconds),
        settings=settings,
    )
    login_or_restore_session(context)


def advanced_search(driver) -> None:
    settings = Settings.load()
    profile = load_search_profile(settings)
    context = BotContext(driver=driver, wait=None, settings=settings)
    open_search(context, profile)


def custom_wait(driver, timeout, condition_type, locator_tuple):
    from selenium.webdriver.support.ui import WebDriverWait

    return WebDriverWait(driver, timeout).until(condition_type(locator_tuple))


def click_and_wait(element, delay: float = 1.0) -> None:
    import time

    element.click()
    time.sleep(delay)


__all__ = [
    "advanced_search",
    "check_cookies_and_login",
    "click",
    "click_and_wait",
    "custom_wait",
    "load_data_from_json",
    "save_data_to_json",
    "scroll_to_bottom",
    "wait_for",
]
