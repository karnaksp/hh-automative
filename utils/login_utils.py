"""Backward-compatible login helper."""

from __future__ import annotations

from hh_automative.auth import is_logged_in


def navigate_and_check(probe_page: str, driver) -> bool:
    from selenium.webdriver.support.ui import WebDriverWait

    from hh_automative.browser import BotContext
    from hh_automative.settings import Settings

    settings = Settings.load()
    context = BotContext(
        driver=driver,
        wait=WebDriverWait(driver, settings.timeout_seconds),
        settings=settings,
    )
    driver.get(probe_page)
    return is_logged_in(context)
