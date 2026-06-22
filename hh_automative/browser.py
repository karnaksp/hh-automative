"""Selenium browser helpers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hh_automative.errors import ManualActionRequiredError, SelectorChangedError
from hh_automative.settings import Settings

if TYPE_CHECKING:
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.remote.webdriver import WebDriver
    from selenium.webdriver.remote.webelement import WebElement
    from selenium.webdriver.support.ui import WebDriverWait

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BotContext:
    driver: WebDriver
    wait: WebDriverWait
    settings: Settings


def create_context(settings: Settings) -> BotContext:
    from selenium import webdriver
    from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
    from selenium.webdriver.support.ui import WebDriverWait

    options = _build_chrome_options(settings, user_profile_dir=settings.chrome_user_data_dir)
    try:
        driver = webdriver.Chrome(options=options)
    except (SessionNotCreatedException, WebDriverException) as exc:
        if settings.chrome_user_data_dir is None:
            raise
        LOGGER.warning(
            "Could not start Chrome with user profile %s; retrying with recovery profile: %s",
            settings.chrome_user_data_dir,
            exc,
        )
        recovery_profile_dir = settings.chrome_user_data_dir.with_name(
            f"{settings.chrome_user_data_dir.name}-recovery"
        )
        recovery_profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            driver = webdriver.Chrome(
                options=_build_chrome_options(settings, user_profile_dir=recovery_profile_dir)
            )
        except (SessionNotCreatedException, WebDriverException) as recovery_exc:
            LOGGER.warning(
                "Could not start Chrome with recovery profile %s; retrying without persistent profile: %s",
                recovery_profile_dir,
                recovery_exc,
            )
            driver = webdriver.Chrome(options=_build_chrome_options(settings, user_profile_dir=None))

    wait = WebDriverWait(driver, settings.timeout_seconds)
    return BotContext(driver=driver, wait=wait, settings=settings)


def _build_chrome_options(settings: Settings, *, user_profile_dir: Path | None) -> Options:
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.page_load_strategy = "eager"
    options.add_argument("--start-maximized")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if user_profile_dir is not None:
        options.add_argument(f"--user-data-dir={user_profile_dir.resolve()}")
    if settings.headless:
        options.add_argument("--headless=new")
    if settings.debug_browser:
        options.add_experimental_option("detach", True)
    return options


def wait_for(context: BotContext, condition: Any, description: str) -> WebElement:
    from selenium.common.exceptions import TimeoutException

    try:
        return context.wait.until(condition)
    except TimeoutException as exc:
        capture_diagnostics(context, f"missing-{_slug(description)}")
        raise SelectorChangedError(f"Element not found or not ready: {description}") from exc


def click(context: BotContext, element: WebElement, description: str) -> None:
    from selenium.common.exceptions import WebDriverException

    try:
        context.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();", element
        )
    except WebDriverException as exc:
        capture_diagnostics(context, f"click-failed-{_slug(description)}")
        raise SelectorChangedError(f"Could not click element: {description}") from exc


def scroll_to_bottom(context: BotContext, delay: float = 0.5, max_steps: int = 20) -> None:
    last_height = context.driver.execute_script("return document.body.scrollHeight")
    for _ in range(max_steps):
        context.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(delay)
        new_height = context.driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            return
        last_height = new_height


def detect_manual_checkpoint(context: BotContext) -> None:
    from selenium.webdriver.common.by import By

    page_text = context.driver.find_element(By.TAG_NAME, "body").text.lower()
    checkpoint_markers = [
        "captcha",
        "проверка безопасности",
        "докажите, что вы не робот",
        "подтвердите, что вы не робот",
    ]
    if any(marker in page_text for marker in checkpoint_markers):
        capture_diagnostics(context, "manual-action-required")
        raise ManualActionRequiredError("Manual checkpoint or CAPTCHA is present.")


def capture_diagnostics(context: BotContext, reason: str) -> Path:
    diagnostics_dir = context.settings.diagnostics_dir
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    base_path = diagnostics_dir / f"{stamp}-{_slug(reason)}"
    screenshot_path = base_path.with_suffix(".png")
    html_path = base_path.with_suffix(".html")
    try:
        context.driver.save_screenshot(str(screenshot_path))
        html_path.write_text(context.driver.page_source, encoding="utf-8")
        LOGGER.info("Saved diagnostics: %s and %s", screenshot_path, html_path)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not mask original error
        LOGGER.warning("Could not save diagnostics for %s: %s", reason, exc)
    return base_path


def close_context(context: BotContext) -> None:
    from selenium.common.exceptions import WebDriverException

    if context.settings.debug_browser:
        LOGGER.info("Debug browser mode enabled; leaving Chrome window open.")
        return
    try:
        context.driver.quit()
    except WebDriverException as exc:
        LOGGER.warning("Could not close Chrome cleanly: %s", exc)


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")[:80]
