"""Authentication flow and session persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from time import sleep

from hh_automative.browser import (
    BotContext,
    capture_diagnostics,
    click,
    detect_manual_checkpoint,
    wait_for,
)
from hh_automative.errors import LoginFailedError, ManualActionRequiredError

LOGGER = logging.getLogger(__name__)


def login_or_restore_session(context: BotContext) -> None:
    context.driver.get(context.settings.search_link)
    if is_logged_in(context):
        LOGGER.info("Already logged in; skipping browser login flow.")
        return

    if _session_files_exist(context):
        _restore_session(context)
        context.driver.get(context.settings.search_link)
        if is_logged_in(context):
            LOGGER.info("Restored hh.ru session from auth files.")
            _save_session(context)
            return
        LOGGER.info("Stored session is invalid; logging in again.")

    login(context)
    context.driver.get(context.settings.search_link)
    if not is_logged_in(context):
        capture_diagnostics(context, "login-not-confirmed")
        raise LoginFailedError("Login finished, but authenticated menu was not detected.")
    _save_session(context)


def login(context: BotContext) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    context.driver.get(context.settings.login_page)
    _open_login_form(context)
    detect_manual_checkpoint(context)

    username_element, calling_code_input = _find_username_input(
        context, context.settings.username
    )
    if username_element is None:
        raise LoginFailedError("Could not find a usable login identifier input.")
    _set_login_value(
        context,
        username_element,
        context.settings.username,
        calling_code_input=calling_code_input,
    )
    _open_password_mode(context)
    _ensure_password_form_open(context)
    _detect_sms_code_required(context)
    password = wait_for(
        context,
        EC.element_to_be_clickable((By.XPATH, '//input[@type="password"]')),
        "password input",
    )
    _set_input_value(context, password, context.settings.password)

    submit = _wait_for_visible_any(
        context,
        [
            "//button[@data-qa='account-login-submit']",
            '//*[self::button or @role="button"][normalize-space(.) = "Войти"]',
        ],
        "login submit button",
    )
    click(context, submit, "login submit button")
    detect_manual_checkpoint(context)


def _open_login_form(context: BotContext) -> None:
    _choose_applicant_role_if_present(context)
    if _is_credential_step(context):
        return

    login_button = _find_login_button(context)
    if login_button is None:
        return
    click(context, login_button, "login button")
    _wait_for(context, lambda _driver: _is_credential_step(context), "credential step")


def _find_login_button(context: BotContext):
    from selenium.webdriver.common.by import By

    # Prefer an explicit "Войти" button to avoid accidental navigation controls.
    exact = _visible_elements(
        context.driver.find_elements(
            By.XPATH,
            '//button[normalize-space(.)="Войти" or @type="submit" and @data-qa="submit-button"]'
        )
    )
    if exact:
        return exact[-1]

    fallback = _visible_elements(
        context.driver.find_elements(
            By.XPATH,
            '//*[self::button or @role="button"][contains(normalize-space(.), "Войти")]'
        )
    )
    if fallback:
        return fallback[-1]
    return None


def _wait_for(context: BotContext, predicate, description: str):
    from selenium.common.exceptions import TimeoutException

    try:
        return context.wait.until(lambda _: predicate(context))
    except TimeoutException as exc:
        from hh_automative.errors import SelectorChangedError

        capture_diagnostics(context, f"missing-{description.replace(' ', '-')}")
        raise SelectorChangedError(f"Page did not reach expected state: {description}") from exc


def _is_credential_step(context: BotContext) -> bool:
    from selenium.webdriver.common.by import By

    if _visible_elements(
        context.driver.find_elements(By.XPATH, '//*[starts-with(@data-qa, "credential-type-")]')
    ):
        return True
    if _visible_elements(
        context.driver.find_elements(
            By.XPATH,
            '//input[@data-qa="applicant-login-input-email"] |'
            ' //input[@data-qa="magritte-phone-input-national-number-input"]',
        )
    ):
        return True
    return bool(
        _visible_elements(
            context.driver.find_elements(
                By.XPATH,
                '//input[@type="text" or @type="email" or @type="tel" or not(@type)]',
            )
        )
    )


def wait_for_manual_login(context: BotContext, timeout_seconds: int = 300) -> None:
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    context.driver.get(context.settings.login_page)
    LOGGER.info("Complete login manually in the opened browser. Waiting up to %s seconds.", timeout_seconds)
    try:
        WebDriverWait(context.driver, timeout_seconds).until(
            EC.presence_of_element_located((By.XPATH, '//a[@data-qa="mainmenu_myResumes"]'))
        )
    except TimeoutException as exc:
        raise LoginFailedError("Manual login timed out before authenticated menu appeared.") from exc
    _save_session(context)
    LOGGER.info("Manual login completed and session was saved.")


def is_logged_in(context: BotContext) -> bool:
    from urllib.parse import urlparse

    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    try:
        current_url = context.driver.current_url.lower()
        parsed = urlparse(current_url)
        if "account/login" in parsed.path or "account/signup" in parsed.path:
            return False

        # Use stable authenticated-only markers. Anonymous hh.ru pages commonly miss all of them.
        if parsed.path.startswith("/applicant/") and _has_authenticated_menu(context):
            return True

        if _has_authenticated_menu(context):
            return True

        # Some authenticated variants show only profile menu items on the main page.
        homepage_nav_markers = _visible_elements(
            context.driver.find_elements(
                By.XPATH,
                '//*[contains(@data-qa, "profileAndResumes") or contains(@href, "/applicant/resumes") or contains(@href, "/applicant/my_resumes")]'
            )
        )
        if homepage_nav_markers:
            return True

        # Direct check on applicant hub reduces false positives on public pages.
        original_url = context.driver.current_url
        context.driver.get("https://hh.ru/applicant/resumes")
        if "/account/login" in context.driver.current_url.lower():
            context.driver.get(original_url)
            return False
        context.wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//a[@data-qa="mainmenu_myResumes"]')
            )
        )
        context.driver.get(original_url)
        return True
    except TimeoutException:
        return False


def _has_authenticated_menu(context: BotContext) -> bool:
    from selenium.webdriver.common.by import By

    return bool(
        context.driver.find_elements(By.XPATH, '//a[@data-qa="mainmenu_myResumes"]')
        or context.driver.find_elements(By.XPATH, '//*[@data-qa="mainmenu_profileAndResumes"]')
        or context.driver.find_elements(By.XPATH, '//*[@data-qa="profileAndResumes-button"]')
        or context.driver.find_elements(By.XPATH, '//a[contains(@href, "/applicant/my_resumes")]')
    )


def _choose_applicant_role_if_present(context: BotContext) -> None:
    from selenium.webdriver.common.by import By

    body_text = context.driver.find_element(By.TAG_NAME, "body").text
    if "Я ищу работу" not in body_text or "Профиль соискателя" not in body_text:
        return

    applicant_cards = _visible_elements(
        context.driver.find_elements(
            By.XPATH,
            '//*[contains(normalize-space(.), "Я ищу работу") '
            'and contains(normalize-space(.), "Профиль соискателя")]',
        )
    )
    if applicant_cards:
        click(context, applicant_cards[-1], "applicant role card")
        sleep(0.5)

    login_buttons = _visible_elements(
        context.driver.find_elements(
            By.XPATH,
            '//*[self::button or @role="button"][normalize-space(.) = "Войти"]',
        )
    )
    if not login_buttons:
        login_buttons = _visible_elements(
            context.driver.find_elements(
                By.XPATH,
                '//*[contains(normalize-space(.), "Войти") '
                'and not(contains(normalize-space(.), "Зарегистрироваться"))]',
            )
        )
    if not login_buttons:
        return
    click(context, login_buttons[-1], "applicant role login button")
    sleep(2)


def _find_username_input(context: BotContext, username: str):
    from selenium.webdriver.common.by import By

    is_email = "@" in username
    if is_email:
        email_inputs = _visible_elements(
            context.driver.find_elements(By.XPATH, '//input[@data-qa="applicant-login-input-email"]')
        )
        if email_inputs:
            return email_inputs[-1], None

    _select_credential_mode(context, "EMAIL" if is_email else "PHONE")

    if is_email:
        _wait_for(context, lambda _driver: bool(_visible_elements(
            context.driver.find_elements(By.XPATH, '//input[@data-qa="applicant-login-input-email"]')
        )), "email input")
        email_inputs = _visible_elements(
            context.driver.find_elements(By.XPATH, '//input[@data-qa="applicant-login-input-email"]')
        )
        if email_inputs:
            return email_inputs[-1], None

    call_code = _visible_elements(
        context.driver.find_elements(By.XPATH, '//input[@data-qa="magritte-phone-input-calling-code-input"]')
    )
    national_inputs = _visible_elements(
        context.driver.find_elements(By.XPATH, '//input[@data-qa="magritte-phone-input-national-number-input"]')
    )
    if national_inputs:
        return national_inputs[-1], call_code[-1] if call_code else None

    direct_inputs = _visible_elements(
        context.driver.find_elements(By.XPATH, '//input[@type="text" and not(@type="hidden")]')
    )
    if direct_inputs:
        return direct_inputs[-1], None
    return None, None


def _select_credential_mode(context: BotContext, mode: str) -> None:
    from selenium.webdriver.common.by import By

    options = _visible_elements(
        context.driver.find_elements(By.XPATH, f'//*[starts-with(@data-qa, "credential-type-{mode}")]')
    )
    if not options:
        return
    _native_click(context, options[-1])
    sleep(0.5)


def _prepare_password_login(context: BotContext) -> None:
    from selenium.webdriver.common.by import By

    if _visible_elements(context.driver.find_elements(By.XPATH, '//input[@type="password"]')):
        return

    password_buttons = _find_password_login_buttons(context)
    if password_buttons:
        try:
            _click_password_button(context, password_buttons[-1])
        except Exception:
            _native_click(context, password_buttons[-1])
        sleep(1.0)
        return

    # Some hh.ru variants expose password flow through a legacy expand control.
    legacy_expand = _visible_elements(
        context.driver.find_elements(By.XPATH, '//button[@data-qa="expand-login-by-password"]')
    )
    if legacy_expand:
        _native_click(context, legacy_expand[-1])
        sleep(1.0)


def _find_password_login_buttons(context: BotContext):
    from selenium.webdriver.common.by import By

    xpaths = [
        # Direct password-login entry points (prefer explicit "enter with password").
        '//*[self::button or @role="button"][normalize-space(.)="Войти с паролем"]',
        '//*[self::button or @role="button"][normalize-space(.)="Войти через пароль"]',
        '//*[self::button or self::a or @role="button" or @role="link"][contains(normalize-space(.), "Войти с паролем")]',
        '//*[self::button or self::a or @role="button" or @role="link"][contains(normalize-space(.), "Войти через пароль")]',
        '//button[@data-qa="expand-login-by-password"]',
        '//button[@data-qa="login-by-password"]',
        '//a[@data-qa="login-by-password"]',
        '//*[self::button or self::a or @role="button" or @role="link"][contains(normalize-space(.), "Введите пароль")]',
        '//*[self::button or @role="button" or @role="link"][contains(@aria-label, "пароль")]',
    ]
    found: list[object] = []
    for xpath in xpaths:
        found.extend(_visible_elements(context.driver.find_elements(By.XPATH, xpath)))
    return found


def _ensure_password_form_open(context: BotContext) -> None:
    from selenium.webdriver.common.by import By

    if _visible_elements(context.driver.find_elements(By.XPATH, '//input[@type="password"]')):
        return

    for _ in range(4):
        _prepare_password_login(context)
        if _visible_elements(context.driver.find_elements(By.XPATH, '//input[@type="password"]')):
            return
        _detect_sms_code_required(context)
        _prepare_by_clicking_continue(context)
        if _visible_elements(context.driver.find_elements(By.XPATH, '//input[@type="password"]')):
            return
        _detect_sms_code_required(context)
        sleep(0.5)

    _detect_sms_code_required(context)
    body_text = context.driver.find_element(By.TAG_NAME, "body").text.lower()
    raise LoginFailedError(f"Password login form did not open. Page hints: {body_text[:120]}")


def _click_password_button(context: BotContext, button) -> None:
    context.driver.execute_script(
        """
        const button = arguments[0];
        button.focus();
        const evtOptions = { bubbles: true, cancelable: true, view: window };
        button.dispatchEvent(new MouseEvent("pointerover", evtOptions));
        button.dispatchEvent(new MouseEvent("mouseover", evtOptions));
        button.dispatchEvent(new MouseEvent("mousedown", evtOptions));
        button.dispatchEvent(new FocusEvent("focus", { bubbles: true, view: window }));
        button.dispatchEvent(new MouseEvent("mouseup", evtOptions));
        button.dispatchEvent(new MouseEvent("click", evtOptions));
        """,
        button,
    )


def _open_password_mode(context: BotContext) -> None:
    # Always force password login path if present. This avoids accidental SMS-only flow.
    _prepare_password_login(context)


def _prepare_by_clicking_continue(context: BotContext) -> None:
    from selenium.webdriver.common.by import By

    continue_buttons = _visible_elements(
        context.driver.find_elements(
            By.XPATH,
            '//*[self::button or @role="button"][normalize-space(.)="Дальше"]',
        )
    )
    if not continue_buttons:
        return
    click(context, continue_buttons[-1], "continue to login method")
    sleep(0.8)


def _detect_sms_code_required(context: BotContext) -> None:
    from selenium.webdriver.common.by import By

    body_text = context.driver.find_element(By.TAG_NAME, "body").text.lower()
    if "введите код из смс" in body_text or "отправили на" in body_text:
        raise ManualActionRequiredError("SMS code is required. Run `python -m hh_automative login --manual`.")


def _set_login_value(
    context: BotContext,
    element,
    value: str,
    *,
    calling_code_input=None,
) -> None:
    if "@" not in value:
        digits = "".join(char for char in value if char.isdigit())
        if len(digits) > 10:
            digits = digits[-10:]
        if calling_code_input is not None:
            code = _calling_code_from_value(value)
            if code and code != "+":
                _set_input_value(context, calling_code_input, code)
            value = digits
        else:
            value = digits
    _set_input_value(context, element, value)


def _calling_code_from_value(value: str) -> str:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) >= 11 and digits.startswith("8"):
        return "+7"
    if len(digits) >= 1 and digits.startswith("7"):
        return "+7"
    return "+7"


def _set_input_value(context: BotContext, element, value: str) -> None:
    element.click()
    context.driver.execute_script(
        """
        const element = arguments[0];
        const value = arguments[1];
        const prototype = element instanceof HTMLTextAreaElement
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, "value").set;
        setter.call(element, value);
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
        """,
        element,
        value,
    )


def _native_click(context: BotContext, element) -> None:
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.action_chains import ActionChains

    context.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    try:
        element.click()
    except WebDriverException:
        ActionChains(context.driver).move_to_element(element).click().perform()


def _wait_for_visible_any(context: BotContext, xpaths: list[str], description: str):
    from selenium.common.exceptions import TimeoutException

    def find_visible(_driver):
        for xpath in xpaths:
            elements = _visible_elements(context.driver.find_elements("xpath", xpath))
            if elements:
                return elements[-1]
        return False

    try:
        return context.wait.until(find_visible)
    except TimeoutException as exc:
        from hh_automative.browser import capture_diagnostics
        from hh_automative.errors import SelectorChangedError

        capture_diagnostics(context, f"missing-{description}")
        raise SelectorChangedError(f"Element not found or not ready: {description}") from exc


def _visible_elements(elements):
    return [
        element
        for element in elements
        if element.is_displayed() and element.size.get("height", 0) > 0
    ]


def _session_files_exist(context: BotContext) -> bool:
    return (
        context.settings.cookies_path.exists()
        and context.settings.local_storage_path.exists()
    )


def _restore_session(context: BotContext) -> None:
    cookies = _read_json(context.settings.cookies_path)
    local_storage = _read_json(context.settings.local_storage_path)
    for cookie in cookies:
        context.driver.add_cookie(cookie)
    for key, value in local_storage.items():
        context.driver.execute_script(
            "window.localStorage.setItem(arguments[0], arguments[1]);", key, value
        )


def _save_session(context: BotContext) -> None:
    context.settings.cookies_path.parent.mkdir(parents=True, exist_ok=True)
    context.settings.local_storage_path.parent.mkdir(parents=True, exist_ok=True)
    context.settings.cookies_path.write_text(
        json.dumps(context.driver.get_cookies(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    local_storage = context.driver.execute_script(
        """
        const data = {};
        for (const key of Object.keys(window.localStorage)) {
            data[key] = window.localStorage.getItem(key);
        }
        return data;
        """
    )
    context.settings.local_storage_path.write_text(
        json.dumps(local_storage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))
