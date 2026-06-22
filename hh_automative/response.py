"""Vacancy response flow."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from hh_automative.ai_assist import (
    build_cover_letter_prompt,
    build_questionnaire_prompt,
    validate_cover_letter_grounding,
    validate_cover_letter_payload_shape,
    validate_questionnaire_answer_text,
    validate_questionnaire_payload_shape,
)
from hh_automative.analytics import DuckDBAnalytics
from hh_automative.browser import BotContext, click, scroll_to_bottom, wait_for
from hh_automative.error_taxonomy import review_metadata
from hh_automative.errors import (
    LLMProviderError,
    LoginFailedError,
    QuestionsRequiredError,
    ResponseFormUnavailableError,
    ResponseNotConfirmedError,
)
from hh_automative.hh_form import (
    apply_structured_answers,
    extract_cover_letter_field,
    extract_response_questions,
    extract_vacancy_text,
    find_suspicious_choice_fields,
    validate_structured_answers,
)
from hh_automative.llm import LLMClient
from hh_automative.models import ActionResult, ResumeProfile, Status, Vacancy
from hh_automative.resumes import choose_resume
from hh_automative.settings import Settings

LOGGER = logging.getLogger(__name__)

_SUBMIT_TEXT_MARKERS: tuple[str, ...] = (
    "отправить",
    "отклик",
    "подать",
    "submit",
    "respond",
    "response",
    "send",
)

_SUCCESS_TEXT_MARKERS: tuple[str, ...] = (
    "вы откликнулись",
    "отклик отправлен",
    "отклик уже отправлен",
)


@dataclass(slots=True)
class ResponseAutomation:
    llm: LLMClient | None = None
    analytics: DuckDBAnalytics | None = None
    profile_name: str = ""


def respond_to_vacancy(
    context: BotContext,
    settings: Settings,
    resume_profile: ResumeProfile,
    vacancy: Vacancy,
    dry_run: bool,
    resume_texts: dict[str, str],
    resume_fallback_text: str,
    automation: ResponseAutomation | None = None,
) -> tuple[ActionResult, str, str, str, str]:
    selected_resume = ""
    selected_code = ""
    resume_text = resume_fallback_text
    if _already_responded(context):
        return (
            ActionResult(Status.SKIPPED, "Already responded on hh.ru page."),
            "",
            "",
            "",
            resume_text,
        )

    if dry_run:
        selected_resume, selected_code = resume_profile.choose_resume_code(vacancy.title)
        resume_text = _resolve_resume_text(selected_code, resume_texts, resume_fallback_text)
        vacancy_text = extract_vacancy_text(context)
        return (
            ActionResult(Status.DRY_RUN, "Dry run: response was not submitted."),
            selected_resume,
            selected_code,
            vacancy_text,
            resume_text,
        )

    vacancy_text = extract_vacancy_text(context)
    _open_response_form(context, vacancy)
    selected_resume, selected_code = choose_resume(context, resume_profile, vacancy.title)
    resume_text = _resolve_resume_text(selected_code, resume_texts, resume_fallback_text)
    _handle_questions_if_present(
        context, settings, vacancy, vacancy_text, automation, resume_text=resume_text
    )
    form_candidates = _collect_response_form_candidates(context)
    cover_letter_field = _fill_cover_letter_if_available(
        context, settings, vacancy, vacancy_text, automation, resume_text
    )
    submit_button = _find_submit_button(
        context,
        cover_letter_field=cover_letter_field,
        form_candidates=form_candidates,
    )
    _submit_response(
        context,
        submit_button,
        form_candidates=form_candidates,
        cover_letter_field=cover_letter_field,
    )
    _wait_for_response_submission_result(context)
    return (
        ActionResult(Status.SUCCESS, "Response submitted."),
        selected_resume,
        selected_code,
        vacancy_text,
        resume_text,
    )


def _resolve_resume_text(
    selected_resume_code: str,
    resume_texts: dict[str, str],
    fallback_text: str,
) -> str:
    if selected_resume_code and resume_texts.get(selected_resume_code):
        return resume_texts[selected_resume_code]
    if fallback_text:
        return fallback_text
    return ""


def _already_responded(context: BotContext) -> bool:
    body_text = _page_text(context)
    return _page_text_contains_response_success(body_text)


def _page_text_contains_response_success(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split())
    return any(marker in normalized for marker in _SUCCESS_TEXT_MARKERS)


def _open_response_form(context: BotContext, vacancy) -> None:
    vacancy_id = vacancy.vacancy_id or _vacancy_id_from_url(context.driver.current_url)
    if vacancy_id:
        response_url = _build_response_url(vacancy_id)
        context.driver.get(response_url)
        try:
            state = _wait_for_response_flow_entry(context)
            if state == "login":
                raise LoginFailedError(
                    "Login session did not survive: direct vacancy response URL redirected to login."
                )
            return
        except ResponseFormUnavailableError:
            LOGGER.info("Direct response page did not open; trying vacancy response button.")

    response_button = _find_response_button(context)
    click(context, response_button, "vacancy response button")
    try:
        state = _wait_for_response_flow_entry(context)
        if state == "login":
            raise LoginFailedError(
                "Login session did not survive: vacancy response redirected to login page."
            )
        return
    except ResponseFormUnavailableError:
        LOGGER.info("Response page did not open after button click; navigating directly.")

    vacancy_id = vacancy.vacancy_id or _vacancy_id_from_url(context.driver.current_url)
    if not vacancy_id:
        vacancy_id = vacancy.vacancy_id
    response_url = _build_response_url(vacancy_id)
    context.driver.get(response_url)

    try:
        state = _wait_for_response_flow_entry(context)
        if state == "login":
            raise LoginFailedError(
                "Login session did not survive: direct vacancy response URL redirected to login."
            )
    except ResponseFormUnavailableError as exc:
        raise ResponseFormUnavailableError(
            "Could not open vacancy response form or detect login redirect."
        ) from exc


def _wait_for_response_flow_entry(
    context: BotContext,
    timeout_seconds: float | None = None,
) -> str:
    timeout = timeout_seconds or max(4.0, float(context.settings.timeout_seconds))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _response_ready_or_redirect(context)
        if state:
            return state
        if _confirm_cross_country_response_if_present(context, timeout_seconds=0.5):
            time.sleep(0.2)
            continue
        time.sleep(0.2)
    raise ResponseFormUnavailableError(
        "Could not open vacancy response form or detect login redirect."
    )


def _response_ready_or_redirect(context: BotContext) -> str | None:
    from selenium.webdriver.common.by import By

    current_url = context.driver.current_url.lower()
    if "/account/login" in current_url:
        return "login"
    if "applicant/vacancy_response" in current_url:
        return "open"

    open_selectors = [
        '//button[@data-qa="vacancy-response-submit-popup"]',
        '//*[@id="RESPONSE_MODAL_FORM_ID"]',
        '//form[@name="vacancy_response"]',
        '//*[@data-qa="add-cover-letter"]',
        '//*[@data-qa="response-popup-close"]',
        '//*[contains(text(), "Введите сопроводительное письмо")]',
        '//*[@role="dialog"]//*[contains(text(), "Отклик на вакансию")]',
        '//*[contains(text(), "Для отклика необходимо ответить на несколько вопросов работодателя")]',
        '//*[contains(text(), "Пожалуйста, ответьте на вопросы ниже")]',
        '//*[contains(text(), "Резюме для отклика")]',
    ]
    for selector in open_selectors:
        if context.driver.find_elements(By.XPATH, selector):
            return "open"
    return None


def _vacancy_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    if "/vacancy/" not in parsed.path:
        return ""
    vacancy_id = parsed.path.rstrip("/").split("/")[-1]
    return vacancy_id if vacancy_id.isdigit() else ""


def _build_response_url(vacancy_id: str) -> str:
    if not vacancy_id:
        raise ResponseFormUnavailableError("Cannot build vacancy response URL without vacancy id.")
    query = urlencode({"vacancyId": vacancy_id})
    return urlunparse(("https", "hh.ru", "/applicant/vacancy_response", "", query, ""))


def _find_response_button(context: BotContext):
    selectors = [
        '//a[@data-qa="vacancy-response-link-top"]',
        '//button[@data-qa="vacancy-response-link-top"]',
        '//a[contains(@data-qa, "vacancy-response")]',
        '//button[contains(@data-qa, "vacancy-response")]',
        '//a[contains(@data-qa, "response")]',
        '//button[contains(@data-qa, "response")]',
        '//a[contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "откликнуться")]',
        '//button[contains(translate(text(), "ОТКЛИКНУТСЯ", "откликнуться"), "откликнуться")]',
        '//a[contains(translate(text(), "ОТВЕТИТЬ", "ответить"), "ответить")]',
        '//button[contains(translate(text(), "ОТВЕТИТЬ", "ответить"), "ответить")]',
        '//a[@role="button" and (contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "откликнуться") or contains(translate(text(), "ОТПРАВИТЬ", "отправить"), "отправить"))]',
        '//button[@role="button" and (contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "откликнуться") or contains(translate(text(), "ОТПРАВИТЬ", "отправить"), "отправить"))]',
        '//span[@role="button" and (contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "откликнуться") or contains(translate(text(), "ОТВЕТИТЬ", "ответить"), "ответить") or contains(translate(text(), "ОТПРАВИТЬ", "отправить"), "отправить"))]',
        '//*[self::button or self::a or self::span or self::div][@role="button" and (contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "откликнуться") or contains(translate(text(), "ОТВЕТИТЬ", "ответить"), "ответить") or contains(translate(text(), "ОТПРАВИТЬ", "отправить"), "отправить"))]',
        '//div[@role="button" and contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "откликнуться")]',
    ]
    candidates: list[Any] = []
    for selector in selectors:
        candidates.extend(_collect_visible_candidates(context.driver, selector))

    candidates.extend(
        _collect_visible_candidates(
            context.driver,
            '//a | //button | //span[@role="button"] | //div[@role="button"]',
        )
    )

    candidates = [button for button in candidates if _is_response_button(button)]
    if not candidates:
        raise ResponseFormUnavailableError("Response button was not found.")

    return _pick_best_response_button(candidates)


def _pick_best_response_button(buttons: list[Any]) -> Any:
    best = None
    best_score = -1
    for button in buttons:
        score = _score_response_button(button)
        if score > best_score:
            best = button
            best_score = score
        if score == 1000:
            break
    return best


def _score_response_button(button: Any) -> int:
    text = _control_label(button)
    aria = (button.get_attribute("aria-label") or "").strip().casefold()
    title = (button.get_attribute("title") or "").strip().casefold()
    data_qa = (button.get_attribute("data-qa") or "").strip().casefold()
    cls = (button.get_attribute("class") or "").strip().casefold()
    name = (button.get_attribute("name") or "").strip().casefold()
    href = (button.get_attribute("href") or "").strip()
    score = 0

    if _is_submit_label(text):
        score += 4
    if _is_submit_label(aria):
        score += 3
    if _is_submit_label(title):
        score += 2
    if href and _is_submit_label(href.casefold()):
        score += 1
    if "vacancy" in data_qa or "response" in data_qa or "apply" in data_qa:
        score += 2
    if name in {"response", "respond", "submit", "apply"}:
        score += 2
    if "btn" in cls:
        score += 1
    if _is_submit_label(text + " " + aria + " " + title):
        score += 3
    return score


def _is_response_button(button: Any) -> bool:
    if not _is_displayed_enabled(button):
        return False
    text_candidates = _collect_text_candidates(button)
    for candidate in text_candidates:
        if _is_submit_label(candidate):
            return True
    if button.get_attribute("role") == "button":
        return _is_submit_label((button.get_attribute("data-qa") or "").casefold())
    href = button.get_attribute("href") or ""
    return bool(href and _is_submit_label(href.casefold()))


def _collect_text_candidates(element: Any) -> list[str]:
    candidates = [
        (element.text or "").strip(),
        (element.get_attribute("aria-label") or "").strip(),
        (element.get_attribute("title") or "").strip(),
        (element.get_attribute("value") or "").strip(),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _find_submit_button(
    context: BotContext,
    *,
    cover_letter_field: Any | None = None,
    form_candidates: list[Any] | None = None,
):
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    if form_candidates is None:
        form_candidates = _collect_response_form_candidates(context)
    submit_candidates = _collect_submit_candidates(
        context,
        form_candidates=form_candidates,
        cover_letter_field=cover_letter_field,
    )
    if submit_candidates:
        scored = _pick_best_submit_candidate(
            submit_candidates, form_candidates, cover_letter_field=cover_letter_field
        )
        if scored is not None:
            return scored

    selectors = [
        '//form[@action="/applicant/vacancy_response/edit_ajax"]//button[@type="submit"]',
        '//form[@action="/applicant/vacancy_response"]//button[@type="submit"]',
        '//form[@action="/applicant/vacancy_response/edit_ajax"]//input[@type="submit"]',
        '//form[@action="/applicant/vacancy_response"]//input[@type="submit"]',
        '//button[@data-qa="vacancy-response-submit-popup"]',
        '//button[@data-qa="vacancy-response-submit"]',
        '//button[@data-qa="vacancy-response-submit-top"]',
        '//button[contains(@class, "vacancy-response-submit")]',
        '//button[@name="response"]',
        '//button[contains(@type, "submit")]',
        '//input[@type="submit"]',
    ]
    for selector in selectors:
        try:
            element = wait_for(
                context,
                EC.element_to_be_clickable((By.XPATH, selector)),
                f"response submit button ({selector})",
            )
            return element
        except TimeoutException:
            continue

    submit_buttons = context.driver.find_elements(
        By.XPATH,
        '//button | //input[@type="submit"] | //input[@type="button"] | //a[@role="button"] | //span[@role="button"] | //div[@role="button"]',
    )
    for button in submit_buttons:
        if not _is_displayed_enabled(button):
            continue
        text = _control_label(button)
        aria = (button.get_attribute("aria-label") or "").strip().casefold()
        title = (button.get_attribute("title") or "").strip().casefold()
        if (
            not _is_submit_label(text)
            and not _is_submit_label(aria)
            and not _is_submit_label(title)
        ):
            continue
        if _is_submit_button(button):
            return button

    if cover_letter_field is not None:
        for field in form_candidates:
            submit = _find_submit_near_form(field)
            if submit is not None:
                return submit
    fallback = _find_submit_fallback(context, form_candidates, cover_letter_field)
    if fallback is not None:
        return fallback

    raise ResponseFormUnavailableError("Response submit button was not found.")


def _submit_response(
    context: BotContext,
    submit_button: Any | None,
    *,
    form_candidates: list[Any] | None = None,
    cover_letter_field: Any | None = None,
) -> None:
    if submit_button is not None:
        try:
            click(context, submit_button, "response submit button")
            _confirm_cross_country_response_if_present(context)
            return
        except Exception as exc:
            LOGGER.warning("Submit click failed, trying form submit fallback: %s", exc)
    if _submit_via_form_fallback(
        context,
        form_candidates=form_candidates,
        cover_letter_field=cover_letter_field,
    ):
        _confirm_cross_country_response_if_present(context)
        return
    raise ResponseFormUnavailableError("Unable to trigger vacancy response submit action.")


def _submit_via_form_fallback(
    context: BotContext,
    *,
    form_candidates: list[Any] | None = None,
    cover_letter_field: Any | None = None,
) -> bool:
    forms = _collect_forms_for_submit(context, form_candidates or [])
    if not forms and cover_letter_field is not None:
        forms = _collect_forms_for_submit(context, [])
    if not forms:
        return False

    for form in _dedupe_dom_elements(forms):
        try:
            if not _is_displayed_enabled(form):
                continue
        except Exception:
            continue
        submitted = context.driver.execute_script(
            """
            const formElement = arguments[0];
            if (!formElement || formElement.tagName.toLowerCase() !== 'form') {
                return false;
            }
            if (typeof formElement.requestSubmit === 'function') {
                formElement.requestSubmit();
                return true;
            }
            formElement.submit();
            return true;
            """,
            form,
        )
        if submitted:
            return True
        LOGGER.debug("Form submit JS returned false for form candidate.")

    # fallback through nearest submit element inside a known form container
    form_buttons = []
    for form in forms:
        form_buttons.extend(
            _collect_visible_candidates(
                form,
                ".//button[@type='submit'] | .//input[@type='submit'] | .//button[contains(@role, 'button')]",
            )
        )
    if form_buttons:
        return _click_first_form_button(context, form_buttons)
    return False


def _confirm_cross_country_response_if_present(context: BotContext, timeout_seconds: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        confirm_button = _find_cross_country_confirm_button(context)
        if confirm_button is not None:
            click(context, confirm_button, "cross-country response confirmation")
            LOGGER.info("Confirmed cross-country response warning modal.")
            return True
        time.sleep(0.2)
    return False


def _wait_for_response_submission_result(
    context: BotContext,
    timeout_seconds: float | None = None,
) -> None:
    timeout = timeout_seconds or max(6.0, float(context.settings.timeout_seconds))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _already_responded(context):
            return
        if _confirm_cross_country_response_if_present(context, timeout_seconds=0.5):
            time.sleep(0.2)
            continue
        if _questions_required(context):
            raise QuestionsRequiredError("Vacancy requires additional answers after submit.")
        current_url = context.driver.current_url.casefold()
        if "/account/login" in current_url:
            raise LoginFailedError("Login session was lost during vacancy response submit.")
        time.sleep(0.25)
    raise ResponseNotConfirmedError(
        "Submit was triggered, but hh.ru did not show a confirmed response success state."
    )


def _find_cross_country_confirm_button(context: BotContext) -> Any | None:
    from selenium.webdriver.common.by import By

    selectors = [
        '//*[@role="dialog"]//button',
        '//*[@role="dialog"]//input[@type="button" or @type="submit"]',
        '//div[contains(@class,"popup") or contains(@class,"modal")]//button',
        '//div[contains(@class,"popup") or contains(@class,"modal")]//input[@type="button" or @type="submit"]',
    ]
    candidates: list[Any] = []
    for selector in selectors:
        candidates.extend(context.driver.find_elements(By.XPATH, selector))
    for candidate in _dedupe_dom_elements(candidates):
        if not _is_displayed_enabled(candidate):
            continue
        if _is_cross_country_confirm_control(candidate):
            return candidate
    return None


def _is_cross_country_confirm_control(control: Any) -> bool:
    texts = _collect_text_candidates(control)
    combined = " ".join(text.casefold() for text in texts if text).strip()
    if not combined:
        return False
    if "все равно откликнуться" in combined:
        return True
    return "другой стране" in combined and not _contains_cancel_signal(combined)


def _page_text(context: BotContext) -> str:
    from selenium.webdriver.common.by import By

    try:
        return context.driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        return ""


def _collect_response_form_candidates(context: BotContext) -> list[Any]:
    from selenium.webdriver.common.by import By

    candidates: list[Any] = []
    direct_forms = context.driver.find_elements(By.XPATH, '//form')
    for form in direct_forms:
        if not _is_displayed_enabled(form):
            continue
        action = (form.get_attribute("action") or "").lower()
        if "vacancy_response" in action or any(
            token in _text_snapshot(form)
            for token in (
                "сопроводительное письмо",
                "вакансия",
                "отклик",
            )
        ):
            candidates.append(form)

    if candidates:
        return _dedupe_dom_elements(_sort_by_response_form_score(candidates))

    # fallback by response block id/class
    candidates.extend(
        context.driver.find_elements(
            By.XPATH,
            '//*[contains(@class,"response-form") or contains(@id,"response") or contains(@data-qa,"response") or contains(@data-qa,"vacancy")]',
        )
    )
    candidates.extend(
        context.driver.find_elements(
            By.XPATH,
            '//*[contains(@class,"vacancy-response") or contains(@id,"vacancy-response") or contains(@role,"form")]',
        )
    )
    response_candidates = [
        candidate
        for candidate in candidates
        if _is_displayed_enabled(candidate) and _is_likely_response_form(candidate)
    ]
    if not response_candidates:
        return []
    return _dedupe_dom_elements(_sort_by_response_form_score(response_candidates))


def _collect_forms_for_submit(context: BotContext, form_candidates: list[Any]) -> list[Any]:
    from selenium.webdriver.common.by import By

    if form_candidates:
        base_forms = [
            candidate
            for candidate in form_candidates
            if _is_displayed_enabled(candidate) and _form_like(candidate)
        ]
        if base_forms:
            return _dedupe_dom_elements(_sort_by_response_form_score(base_forms))

    direct_forms = context.driver.find_elements(By.XPATH, '//form')
    response_forms = [
        candidate
        for candidate in direct_forms
        if _is_displayed_enabled(candidate) and _is_likely_response_form(candidate)
    ]
    fallback_selectors = [
        '//div[contains(@class,"response")][.//form]',
        '//section[contains(@class,"response")]//form',
        '//article[contains(@class,"response")]//form',
        '//form[contains(@class,"response")]',
        '//form[contains(@action,"vacancy_response")]',
    ]
    for selector in fallback_selectors:
        for candidate in context.driver.find_elements(By.XPATH, selector):
            if _is_displayed_enabled(candidate):
                response_forms.append(candidate)
    if response_forms:
        return _dedupe_dom_elements(_sort_by_response_form_score(response_forms))
    return []


def _is_likely_response_form(candidate: Any) -> bool:
    attrs = [
        candidate.get_attribute("class") or "",
        candidate.get_attribute("id") or "",
        candidate.get_attribute("data-qa") or "",
        candidate.get_attribute("role") or "",
    ]
    text = _text_snapshot(candidate)
    combined = " ".join(value.lower() for value in attrs) + " " + text
    markers = (
        "vacancy-response",
        "vacancy_response",
        "response-form",
        "response",
        "vacancy",
        "сопровод",
        "отклик",
        "ваканс",
    )
    return any(marker in combined for marker in markers)


def _sort_by_response_form_score(candidates: list[Any]) -> list[Any]:
    return sorted(candidates, key=_response_form_score, reverse=True)


def _response_form_score(candidate: Any) -> int:
    score = 0
    if not _is_displayed_enabled(candidate):
        return -1
    attrs = [
        candidate.get_attribute("class") or "",
        candidate.get_attribute("id") or "",
        candidate.get_attribute("data-qa") or "",
    ]
    combined = " ".join(value.lower() for value in attrs) + " " + _text_snapshot(candidate)
    if "vacancy-response" in combined or "vacancy_response" in combined:
        score += 7
    if "response" in combined:
        score += 5
    if "form" in candidate.tag_name.lower():
        score += 2
    if "vacancy" in combined:
        score += 3
    if _text_snapshot(candidate):
        score += 1
    if "отклик" in combined or "сопровод" in combined:
        score += 4
    return score


def _dedupe_dom_elements(elements: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[int] = set()
    for element in elements:
        marker = id(element)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(element)
    return deduped


def _collect_submit_candidates(
    context: BotContext,
    form_candidates: list[Any],
    cover_letter_field: Any | None,
) -> list[Any]:
    candidates: list[Any] = []
    response_scopes = _collect_response_submit_scopes(context, form_candidates, cover_letter_field)
    for form in form_candidates:
        form_buttons = _collect_visible_candidates(
            form,
            ".//button | .//input[@type='submit'] | .//input[@type='button'] | .//a[@role='button'] | .//span[@role='button'] | .//div[@role='button']",
        )
        for button in form_buttons:
            if _is_response_related_control(button) or _is_submit_button(button):
                candidates.append(button)

    explicit_selectors = [
        '//button[@data-qa="vacancy-response-submit-popup"]',
        '//button[@data-qa="vacancy-response-submit"]',
        '//button[@data-qa="vacancy-response-submit-top"]',
        '//button[@data-qa="vacancy-response-submit-bottom"]',
        '//button[@data-qa="vacancy-response-send"]',
        '//a[@data-qa="vacancy-response-submit-popup"]',
        '//a[@data-qa="vacancy-response-submit"]',
        '//a[@data-qa="vacancy-response-submit-top"]',
        '//a[@data-qa="vacancy-response-submit-bottom"]',
        '//a[@data-qa="vacancy-response-send"]',
        '//div[@data-qa="vacancy-response-submit-popup"]',
        '//div[@data-qa="vacancy-response-submit"]',
        '//div[@data-qa="vacancy-response-send"]',
        '//div[@role="button"]',
        '//button[@data-qa="vacancy-response-apply"]',
        '//a[@data-qa="vacancy-response-apply"]',
        '//button[contains(@data-qa, "vacancy-response") and contains(@type, "submit")]',
        '//input[contains(@data-qa, "vacancy-response") and @type="submit"]',
        '//button[@type="submit" and (contains(@data-qa,"response") or contains(@class,"submit"))]',
        '//input[@type="submit" and (contains(@data-qa,"response") or contains(@name,"response") or contains(@class,"submit"))]',
        '//input[@type="button" and (contains(@data-qa,"response") or contains(@name,"response") or contains(@class,"submit"))]',
        '//a[@role="button"]',
        '//button[contains(@data-qa, "vacancy-response")]',
        '//button[contains(@class, "vacancy") and contains(@class, "submit")]',
        '//a[contains(@data-qa, "vacancy-response")]',
        '//a[contains(@class, "vacancy") and contains(@class, "submit")]',
        '//span[@role="button" and contains(@data-qa, "vacancy-response")]',
        '//span[@role="button" and (contains(translate(text(), "ОТВЕТИТЬ", "ответить"), "ответить") or contains(translate(text(), "ОТПРАВИТЬ", "отправить"), "отправить"))]',
        '//button[contains(translate(text(), "ОТПРАВИТЬ", "отправить"), "отправить")]',
        '//a[contains(translate(text(), "ОТПРАВИТЬ", "отправить"), "отправить")]',
        '//button[contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "отклик")]',
        '//a[contains(translate(text(), "ОТКЛИКНУТЬСЯ", "откликнуться"), "отклик")]',
        '//button[contains(translate(text(), "ПОДАТЬ", "подать"), "подать")]',
        '//a[contains(translate(text(), "ПОДАТЬ", "подать"), "подать")]',
        '//button[contains(translate(text(), "ОТПРАВЛЯТЬ", "отправить"), "отправ")]',
        '//a[contains(translate(text(), "ОТПРАВЛЯТЬ", "отправить"), "отправ")]',
        '//button[contains(translate(text(), "ПРИНЯТЬ", "принять"), "принять")]',
        '//button[contains(translate(text(), "ЗАВЕРШИТЬ", "завершить"), "завершить")]',
        '//a[contains(translate(text(), "ЗАВЕРШИТЬ", "завершить"), "завершить")]',
        '//input[@type="submit"]',
    ]
    for selector in explicit_selectors:
        candidates.extend(
            button
            for button in (_collect_visible_candidates(context.driver, selector) if hasattr(context.driver, "find_elements") else [])
            if _is_response_related_control(button) or _is_submit_button(button)
        )

    global_buttons = _collect_visible_candidates(
        context.driver,
        '//button | //input[@type="submit"] | //input[@type="button"] | //a[@role="button"] | //span[@role="button"] | //div[@role="button"]',
    )
    for button in global_buttons:
        if _is_submit_button(button):
            candidates.append(button)

    if cover_letter_field is not None:
        for button in _collect_visible_candidates(
            cover_letter_field,
            "../following-sibling::*//button | ../following-sibling::*//a[@role='button'] | ../following-sibling::*//input[@type='submit'] | ../following-sibling::*//input[@type='button']",
        ):
            if _is_response_related_control(button) or _is_submit_button(button):
                candidates.append(button)
        candidates.extend(_collect_nearby_cover_letter_controls(cover_letter_field))

    for scope in response_scopes:
        for button in _collect_visible_candidates(
            scope,
            ".//button | .//input[@type='submit'] | .//input[@type='button'] | .//a[@role='button'] | .//span[@role='button'] | .//div[@role='button']",
        ):
            if _is_submit_control_candidate(button):
                candidates.append(button)

    # dedupe
    return _dedupe_dom_elements(candidates)


def _is_response_related_control(control: Any) -> bool:
    data_qa = (control.get_attribute("data-qa") or "").strip().casefold()
    aria = (control.get_attribute("aria-label") or "").strip().casefold()
    text = _control_label(control)
    name = (control.get_attribute("name") or "").strip().casefold()
    if any(token in data_qa for token in ("vacancy-response", "response", "vacancy_response")):
        return True
    if any(token in name for token in ("response", "respond", "apply", "submit")):
        return True
    if _is_submit_label(text) or _is_submit_label(aria):
        return True
    href = (control.get_attribute("href") or "").strip().casefold()
    return "response" in href and not _contains_cancel_signal(href)


def _collect_nearby_cover_letter_controls(cover_letter_field: Any) -> list[Any]:
    candidates: list[Any] = []

    # walk from the field upward and take the first few ancestors
    # to catch cases where submit button is nested in a response wrapper
    ancestors = list(_iter_control_ancestors(cover_letter_field, limit=4))
    for ancestor in ancestors:
        local = _collect_visible_candidates(
            ancestor,
            ".//button[@type='submit'] | .//input[@type='submit'] | .//button[@type='button'] | "
            ".//input[@type='button'] | .//a[@role='button'] | .//span[@role='button'] | "
            ".//div[@role='button']",
        )
        for button in local:
            if _is_submit_button(button):
                candidates.append(button)
    return candidates


def _collect_response_submit_scopes(
    context: BotContext,
    form_candidates: list[Any],
    cover_letter_field: Any | None,
) -> list[Any]:
    from selenium.webdriver.common.by import By

    scope_selectors = [
        '//div[contains(@class,"popup") and (contains(., "сопроводительное письмо") or contains(., "вакансия") or contains(., "отклик"))]',
        '//div[contains(@class,"modal") and (contains(., "сопроводительное письмо") or contains(., "отклик"))]',
        '//*[contains(@class,"vacancy-response") or contains(@id,"vacancy-response")]',
        '//*[contains(@class,"response-form") or contains(@id,"response")]',
        '//form[@action="/applicant/vacancy_response"]',
        '//form[@action="/applicant/vacancy_response/edit_ajax"]',
    ]
    scopes: list[Any] = []
    for selector in scope_selectors:
        scopes.extend(context.driver.find_elements(By.XPATH, selector))

    for form in form_candidates:
        if form not in scopes:
            scopes.append(form)

    if cover_letter_field is not None:
        scopes.extend(_iter_control_ancestors(cover_letter_field, limit=6))
    deduped = _dedupe_dom_elements(scopes)
    return [scope for scope in deduped if _is_displayed_enabled(scope)]


def _is_submit_control_candidate(control: Any) -> bool:
    if not _is_submit_button(control) and not _is_submit_label(_control_label(control)):
        return False
    if _contains_cancel_signal(_control_label(control)):
        return False
    return not _contains_cancel_signal((control.get_attribute("aria-label") or "").strip())


def _iter_control_ancestors(element: Any, limit: int = 4):
    current = element
    for _ in range(limit):
        yield current
        try:
            current = current.find_element("xpath", "..")
        except Exception:
            return

def _pick_best_submit_candidate(
    candidates: list[Any],
    form_candidates: list[Any],
    *,
    cover_letter_field: Any | None = None,
) -> Any | None:
    best = None
    best_score = -1
    for candidate in candidates:
        score = _score_submit_candidate(
            candidate,
            form_candidates=form_candidates,
            cover_letter_field=cover_letter_field,
        )
        if score > best_score:
            best_score = score
            best = candidate
    if best is not None and best_score >= 1:
        return best
    return None


def _score_submit_candidate(
    candidate: Any,
    form_candidates: list[Any],
    cover_letter_field: Any | None = None,
) -> int:
    score = 0
    text = _control_label(candidate)
    aria = (candidate.get_attribute("aria-label") or "").strip().casefold()
    title = (candidate.get_attribute("title") or "").strip().casefold()
    candidate_type = (candidate.get_attribute("type") or "").strip().casefold()
    candidate_name = (candidate.get_attribute("name") or "").strip().casefold()
    classes = (candidate.get_attribute("class") or "").casefold()
    data_qa = (candidate.get_attribute("data-qa") or "").casefold()

    if _contains_cancel_signal(text) or _contains_cancel_signal(aria) or _contains_cancel_signal(title):
        return -1000
    if _is_submit_button(candidate):
        score += 4
    if _is_submit_label(text):
        score += 3
    if _is_submit_label(aria):
        score += 2
    if _is_submit_label(title):
        score += 2
    if candidate_type == "submit":
        score += 3
    if candidate_name in {"response", "respond", "submit"}:
        score += 2
    if candidate_name in {"submit", "response", "respond", "apply", "send"}:
        score += 1
    if "vacancy-response" in data_qa or "response" in data_qa:
        score += 2
    if any(token in data_qa for token in {"vacancy-response-submit", "send"}):
        score += 3
    if "submit" in classes:
        score += 1
    if _is_response_related_control(candidate):
        score += 2
    if data_qa.startswith("vacancy-response-submit") or "vacancy-response-submit" in data_qa:
        score += 3
    if text == "":
        score -= 1
    if _is_submit_contextual_candidate(candidate):
        score += 1
    if _has_data_qa_class_signal(candidate):
        score += 1

    if cover_letter_field is not None and _is_related_to_cover_letter_field(
        candidate,
        cover_letter_field,
    ):
        score += 3

    if _is_within_response_scope(candidate, form_candidates):
        score += 2
    if _has_close_button_role(candidate):
        score -= 1
    return score


def _has_data_qa_class_signal(candidate: Any) -> bool:
    data_qa = (candidate.get_attribute("data-qa") or "").strip().casefold()
    classes = (candidate.get_attribute("class") or "").strip().casefold()
    return any(token in data_qa for token in ("vacancy-response", "response", "submit", "apply")) or any(
        token in classes for token in ("response", "submit", "apply", "vacancy")
    )


def _is_related_to_cover_letter_field(candidate: Any, cover_letter_field: Any) -> bool:
    candidate_signatures = _collect_element_signatures(_iter_control_ancestors(candidate, limit=20))
    field_signatures = _collect_element_signatures(_iter_control_ancestors(cover_letter_field, limit=20))
    return bool(candidate_signatures.intersection(field_signatures))


def _collect_element_signatures(elements: list[Any]) -> set[str]:
    signatures: set[str] = set()
    for element in elements:
        found = False
        try:
            for marker_name in (
                element.get_attribute("data-qa"),
                element.get_attribute("id"),
                element.get_attribute("name"),
                element.get_attribute("role"),
            ):
                if marker_name:
                    normalized = str(marker_name).strip().casefold()
                    if normalized:
                        signatures.add(normalized)
                        found = True
        except Exception:
            continue
        if not found:
            try:
                text = (element.text or "").strip().lower()
            except Exception:
                text = ""
            if text:
                signatures.add(" ".join(text.split())[:120])
    return signatures


def _is_submit_contextual_candidate(candidate: Any) -> bool:
    text = _control_label(candidate)
    attrs = (
        (candidate.get_attribute("class") or ""),
        (candidate.get_attribute("id") or ""),
        (candidate.get_attribute("name") or ""),
        (candidate.get_attribute("data-qa") or ""),
        (candidate.get_attribute("role") or ""),
        (candidate.get_attribute("href") or ""),
    )
    combined = " ".join(attr.lower() for attr in attrs) + " " + text
    return (
        ("response" in combined)
        or ("vacancy-response" in combined)
        or ("vacancy" in combined and "submit" in combined)
        or ("сопровод" in combined)
        or ("отклик" in combined)
    )


def _is_within_response_scope(candidate: Any, form_candidates: list[Any]) -> bool:
    if not form_candidates:
        return False
    markers = ("vacancy-response", "vacancy_response", "response", "respond", "submit")
    for ancestor in form_candidates:
        try:
            text = (ancestor.text or "").lower()
        except Exception:
            continue
        if any(marker in text for marker in ("сопроводительное", "отклик", "ваканс", "response")):
            return True
        try:
            attrs = [
                ancestor.get_attribute("class") or "",
                ancestor.get_attribute("id") or "",
                ancestor.get_attribute("data-qa") or "",
            ]
            attr_text = " ".join(attrs).lower()
            if any(marker in attr_text for marker in markers):
                return True
        except Exception:
            pass
    try:
        for parent in candidate.find_elements("xpath", "../ancestor::*"):
            parent_id = parent.get_attribute("data-qa") or parent.get_attribute("id") or ""
            if parent_id and any(marker in (parent_id or "").lower() for marker in markers):
                return True
    except Exception:
        pass
    return False


def _collect_visible_candidates(context_or_element: Any, selector: str) -> list[Any]:
    elements = context_or_element.find_elements("xpath", selector)
    return [element for element in elements if _is_displayed_enabled(element)]


def _is_displayed_enabled(element: Any) -> bool:
    try:
        return bool(element.is_displayed() and element.is_enabled())
    except Exception:
        return False


def _is_submit_label(text: str) -> bool:
    if not text:
        return False
    normalized = text.casefold()
    if _contains_cancel_signal(normalized):
        return False
    negatives = ("отмена", "закрыть", "назад", "выйти", "отменить")
    if any(negative in normalized for negative in negatives):
        return False
    if _is_submit_marker(normalized):
        return True
    return "vacancy-response" in normalized


def _is_submit_marker(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in _SUBMIT_TEXT_MARKERS)


def _contains_cancel_signal(text: str) -> bool:
    normalized = text.casefold()
    cancel_markers = ("отмена", "закрыт", "закрой", "назад", "выйт", "отмен")
    return any(marker in normalized for marker in cancel_markers)


def _has_close_button_role(element: Any) -> bool:
    aria = (element.get_attribute("aria-label") or "").strip().casefold()
    text = _control_label(element)
    role = (element.get_attribute("role") or "").strip().casefold()
    href = (element.get_attribute("href") or "").strip().casefold()
    cls = (element.get_attribute("class") or "").strip().casefold()
    candidates = (aria, text, href, role, cls)
    return any(_contains_cancel_signal(value) for value in candidates) or "close" in href


def _is_submit_button(button: Any) -> bool:
    text = _control_label(button)
    if _is_submit_label(text):
        return True
    aria = (button.get_attribute("aria-label") or "").strip().casefold()
    if _is_submit_label(aria):
        return True
    button_type = (button.get_attribute("type") or "").strip().casefold()
    if button_type == "submit":
        return True
    name = (button.get_attribute("name") or "").strip().casefold()
    if name in {"response", "respond", "submit"}:
        return True
    href = button.get_attribute("href") or ""
    href_lower = href.casefold()
    if href and any(token in href_lower for token in ("send", "apply", "response")):
        return True
    role = (button.get_attribute("role") or "").strip().casefold()
    data_qa = (button.get_attribute("data-qa") or "").strip().casefold()
    if role == "button" and _is_submit_label(" ".join([text, aria, data_qa])):
        return True
    return _is_submit_label((button.get_attribute("value") or "").strip().casefold())


def _find_submit_near_form(form_or_container: Any) -> Any | None:
    buttons = _collect_visible_candidates(
        form_or_container,
        ".//button | .//input[@type='submit'] | .//input[@type='button'] | .//a[contains(@role, 'button')]",
    )
    for button in buttons:
        if _is_submit_button(button):
            return button
    # fallback to the latest visible submit-looking control inside the same container
    return next(
        (
            button
            for button in buttons
            if _is_submit_label((button.text or button.get_attribute("value") or "").strip().casefold())
        ),
        None,
    )


def _find_submit_fallback(
    context: BotContext,
    form_candidates: list[Any],
    cover_letter_field: Any | None,
) -> Any | None:
    candidates: list[Any] = []
    if cover_letter_field is not None:
        candidates.extend(_collect_nearby_cover_letter_controls(cover_letter_field))
    candidates.extend(
        _collect_visible_candidates(
            context.driver,
            '//button[@type="submit"] | //input[@type="submit"] | //button[@type="button"] | //input[@type="button"] | //a[@role="button"] | //span[@role="button"] | //div[@role="button"]',
        )
    )

    deduped: list[Any] = []
    seen: set[int] = set()
    for candidate in candidates:
        if not (_is_submit_button(candidate) or _is_submit_label(_control_label(candidate))):
            continue
        marker = id(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(candidate)

    best = None
    best_score = -999
    for candidate in deduped:
        score = _score_submit_candidate(candidate, form_candidates)
        if score > best_score:
            best_score = score
            best = candidate
    if best is not None and best_score >= 1:
        return best
    return None


def _control_label(control: Any) -> str:
    text = (control.text or "").strip()
    if text:
        return text.casefold()
    value = (control.get_attribute("value") or "").strip()
    if value:
        return value.casefold()
    aria = (control.get_attribute("aria-label") or "").strip()
    if aria:
        return aria.casefold()
    title = (control.get_attribute("title") or "").strip()
    return title.casefold()


def _form_like(element: Any) -> bool:
    tag = (element.tag_name or "").strip().lower()
    return tag == "form"


def _click_first_form_button(context: BotContext, buttons: list[Any]) -> bool:
    for button in buttons:
        try:
            if not _is_submit_button(button) and not _is_submit_label(_control_label(button)):
                continue
            click(context, button, "fallback form submit button")
            return True
        except Exception:
            continue
    return False


def _text_snapshot(element: Any) -> str:
    try:
        return (element.text or "").strip().casefold()
    except Exception:
        return ""


def _questions_required(context: BotContext) -> bool:
    from selenium.webdriver.common.by import By

    return bool(
        context.driver.find_elements(
            By.XPATH,
            '//*[contains(text(), "Ответьте на вопросы") or contains(text(), "Вопросы работодателя")]',
        )
    )


def _handle_questions_if_present(
    context: BotContext,
    settings: Settings,
    vacancy: Vacancy,
    vacancy_text: str,
    automation: ResponseAutomation | None,
    resume_text: str = "",
) -> None:
    questions = extract_response_questions(context)
    if not questions and not _questions_required(context):
        return
    if not (
        settings.use_llm_questionnaire
    ) or not (automation and automation.llm):
        raise QuestionsRequiredError("Vacancy requires additional answers.")

    suspicious_fields = find_suspicious_choice_fields(questions)
    if suspicious_fields:
        reason = "manual review required: suspicious questionnaire choice fields: " + "; ".join(
            suspicious_fields
        )
        _log_ai(
            automation,
            "questionnaire",
            "failed",
            vacancy,
            error_reason=reason,
            vacancy_text=vacancy_text,
            resume_text=resume_text,
        )
        raise QuestionsRequiredError(reason)

    prompt = build_questionnaire_prompt(
        vacancy_text=vacancy_text,
        resume_text=resume_text,
        questions=[field.question for field in questions],
    )
    _log_ai(
        automation,
        "questionnaire",
        "prompt_submitted",
        vacancy,
        prompt=prompt,
        vacancy_text=vacancy_text,
        resume_text=resume_text,
    )
    try:
        answer = automation.llm.ask_json(prompt, timeout_seconds=settings.llm_timeout_seconds)
    except LLMProviderError as exc:
        _log_ai(
            automation,
            "questionnaire",
            "failed",
            vacancy,
            prompt=prompt,
            error_reason=str(exc),
            vacancy_text=vacancy_text,
            resume_text=resume_text,
        )
        raise
    try:
        shape_errors = validate_questionnaire_payload_shape(answer.parsed_json)
        if shape_errors:
            raise LLMProviderError("; ".join(shape_errors))
        answer_errors = validate_structured_answers(questions, answer.parsed_json)
        if answer_errors:
            raise LLMProviderError("; ".join(answer_errors))
        text_errors = validate_questionnaire_answer_text(
            answer.parsed_json,
            vacancy_text=vacancy_text,
            resume_text=resume_text,
        )
        if text_errors:
            raise LLMProviderError("; ".join(text_errors))
    except LLMProviderError as exc:
        _log_ai(
            automation,
            "questionnaire",
            "failed",
            vacancy,
            prompt=prompt,
            parsed_json=answer.parsed_json,
            error_reason=str(exc),
            vacancy_text=vacancy_text,
            resume_text=resume_text,
        )
        raise
    if answer.parsed_json.get("needs_human_review"):
        raise QuestionsRequiredError("LLM marked questionnaire as needs_human_review.")
    _log_ai(
        automation,
        "questionnaire",
        "answered",
        vacancy,
        prompt=prompt,
        response_text=answer.raw_text,
        parsed_json=answer.parsed_json,
        vacancy_text=vacancy_text,
        resume_text=resume_text,
    )
    try:
        apply_structured_answers(context, questions, answer.parsed_json)
    except Exception as exc:
        reason = f"manual review required: could not apply questionnaire answers: {exc}"
        _log_ai(
            automation,
            "questionnaire",
            "failed",
            vacancy,
            prompt=prompt,
            response_text=answer.raw_text,
            parsed_json=answer.parsed_json,
            error_reason=reason,
            vacancy_text=vacancy_text,
            resume_text=resume_text,
        )
        raise QuestionsRequiredError(reason) from exc


def _fill_cover_letter_if_available(
    context: BotContext,
    settings: Settings,
    vacancy: Vacancy,
    vacancy_text: str,
    automation: ResponseAutomation | None,
    resume_text: str = "",
) -> None:
    _open_cover_letter_editor(context)
    scroll_to_bottom(context)
    textarea = extract_cover_letter_field(context)
    if textarea is None:
        _log_ai(
            automation,
            "cover_letter",
            "not_available",
            vacancy,
            error_reason="Cover letter textarea was not found on the response form.",
            vacancy_text=vacancy_text,
            resume_text=resume_text,
        )
        return
    message = _cover_letter_text(
        context, settings, vacancy, vacancy_text, automation, resume_text
    )
    if not message:
        return
    context.driver.execute_script(
        """
        const element = arguments[0];
        const value = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype,
            "value"
        ).set;
        setter.call(element, value);
        element.dispatchEvent(new Event("input", { bubbles: true }));
        """,
        textarea,
        message,
    )
    _log_ai(
        automation,
        "cover_letter",
        "inserted",
        vacancy,
        parsed_json={"cover_letter": message},
        vacancy_text=vacancy_text,
        resume_text=resume_text,
    )


def _open_cover_letter_editor(context: BotContext) -> None:
    import contextlib

    from selenium.common.exceptions import TimeoutException

    selectors = [
        '//button[@data-qa="vacancy-response-letter-toggle"]',
        '//button[contains(@data-qa, "vacancy-response") and contains(@data-qa, "letter")]',
        '//button[contains(@data-qa, "vacancy-response-message")]',
        '//button[contains(@data-qa, "response-letter")]',
        '//button[contains(translate(text(), "СОПРОВОДИТЕЛЬНОЕ", "сопроводительное"), "сопроводительное")]',
        '//button[contains(translate(text(), "НЕТ", "нет"), "нет") and contains(translate(text(), "СООБЩЕНИЕ", "сообщение"), "сообщение")]',
        '//button[contains(translate(text(), "СООБЩЕНИЕ", "сообщение"), "сообщение") and contains(translate(text(), "ВАКАНС", "ваканс"), "ваканс")]',
        '//label[contains(translate(text(), "СОПРОВОДИТЕЛЬНОЕ", "сопроводительное"), "сопроводительное")]',
        '//a[contains(translate(text(), "СОПРОВОДИТЕЛЬНОЕ", "сопроводительное"), "сопроводительное")]',
        '//*[contains(@aria-label, "сопровод")]',
        '//div[@role="button" and contains(translate(., "СОПРОВОДИТЕЛЬНОЕ", "сопроводительное"), "сопроводительное")]',
    ]

    for selector in selectors:
        elements = context.driver.find_elements("xpath", selector)
        if not elements:
            continue
        for element in elements:
            if not (element.is_displayed() and element.is_enabled()):
                continue
            try:
                click(context, element, "cover letter toggle")
                with contextlib.suppress(TimeoutException):
                    wait_for(
                        context,
                        lambda driver: bool(_find_cover_letter_textarea(context)),
                        "cover letter textarea appears after toggle",
                    )
                return
            except Exception:
                continue


def _find_cover_letter_textarea(context: BotContext):
    try:
        return extract_cover_letter_field(context)
    except Exception:
        return None


def _cover_letter_text(
    context: BotContext,
    settings: Settings,
    vacancy: Vacancy,
    vacancy_text: str,
    automation: ResponseAutomation | None,
    resume_text: str = "",
) -> str:
    vacancy_context = _vacancy_context_for_prompt(vacancy)
    if not (settings.use_llm_cover_letter and automation and automation.llm):
        _log_ai(
            automation,
            "cover_letter",
            "skipped",
            vacancy,
            error_reason="LLM cover letter generation is disabled or unavailable.",
            vacancy_text=vacancy_text,
            resume_text=resume_text,
        )
        return ""

    if settings.use_llm_cover_letter and automation and automation.llm:
        prompts = [
            build_cover_letter_prompt(
                vacancy_text=vacancy_text,
                resume_text=resume_text,
                strict=True,
                vacancy_context=vacancy_context,
            ),
            build_cover_letter_prompt(
                vacancy_text=vacancy_text,
                resume_text=resume_text,
                strict=False,
                vacancy_context=vacancy_context,
            ),
        ]
        for index, prompt in enumerate(prompts):
            _log_ai(
                automation,
                "cover_letter",
                "prompt_submitted",
                vacancy,
                prompt=prompt,
                vacancy_text=vacancy_text,
                resume_text=resume_text,
            )
            try:
                answer = automation.llm.ask_json(prompt, timeout_seconds=settings.llm_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 - cover letter must not block response flow
                _log_ai(
                    automation,
                    "cover_letter",
                    "failed",
                    vacancy,
                    prompt=prompt,
                    error_reason=str(exc),
                    vacancy_text=vacancy_text,
                    resume_text=resume_text,
                )
                LOGGER.warning(
                    "LLM cover letter generation failed for %s: %s",
                    vacancy.url,
                    exc,
                )
                return ""

            cover_letter = _normalize_cover_letter_text(
                str(answer.parsed_json.get("cover_letter", "")).strip()
            )
            normalized_payload = dict(answer.parsed_json)
            normalized_payload["cover_letter"] = cover_letter
            shape_errors = validate_cover_letter_payload_shape(
                normalized_payload,
                strict=index == 0,
            )
            if shape_errors:
                error = LLMProviderError("; ".join(shape_errors))
                _log_ai(
                    automation,
                    "cover_letter",
                    "failed",
                    vacancy,
                    prompt=prompt,
                    response_text=answer.raw_text,
                    parsed_json=normalized_payload,
                    error_reason=str(error),
                    vacancy_text=vacancy_text,
                    resume_text=resume_text,
                )
                if index + 1 < len(prompts):
                    continue
                LOGGER.warning(
                    "LLM cover letter response rejected for %s: %s",
                    vacancy.url,
                    error,
                )
                return ""
            else:
                grounding_warnings = validate_cover_letter_grounding(
                    normalized_payload,
                    vacancy_text=vacancy_text,
                    resume_text=resume_text,
                    strict=index == 0,
                )
                if grounding_warnings:
                    _log_ai(
                        automation,
                        "cover_letter",
                        "grounding_warning",
                        vacancy,
                        prompt=prompt,
                        response_text=answer.raw_text,
                        parsed_json={**answer.parsed_json, "cover_letter": cover_letter},
                        error_reason="; ".join(grounding_warnings),
                        vacancy_text=vacancy_text,
                        resume_text=resume_text,
                    )
                _log_ai(
                    automation,
                    "cover_letter",
                    "answered",
                    vacancy,
                    prompt=prompt,
                    response_text=answer.raw_text,
                    parsed_json={**answer.parsed_json, "cover_letter": cover_letter},
                    vacancy_text=vacancy_text,
                    resume_text=resume_text,
                )
                return cover_letter
    return ""


def _vacancy_context_for_prompt(vacancy: Vacancy) -> str:
    parts = [vacancy.title]
    if vacancy.company:
        parts.append(vacancy.company)
    if vacancy.url:
        parts.append(vacancy.url)
    return " | ".join(part for part in parts if part)


def _normalize_cover_letter_text(value: str) -> str:
    if not value:
        return ""
    text = value.strip().strip("\n\ufeff")
    text = text.strip('"\'` ')

    # remove code-fence wrappers and simple markdown artefacts if model spilled them
    if text.startswith("```") and text.endswith("```") and len(text) > 6:
        inner = text[3:-3].strip()
        if inner.startswith("json"):
            inner = inner[4:].lstrip()
        text = inner

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    kept: list[str] = []
    greeting_prefixes = ("уважаемый", "добрый день", "здравствуйте", "добрый вечер", "привет")
    for line in lines:
        normalized_line = line.casefold()
        if any(token in normalized_line for token in greeting_prefixes):
            if kept:
                break
            line = re.sub(r"(?i)^(уважаемый|здравствуйте|добрый день|добрый вечер|привет)[^,\.!?]*[,:-]?\s*", "", line).strip()
            if not line:
                continue
        kept.append(line)

    if not kept:
        return ""
    text = "\n".join(kept)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\[\s*[^\]]+\s*\]", "", text)
    text = re.sub(r"(?i)(с\s*уважением,?.*)$", "", text).strip()
    return text

def _log_ai(
    automation: ResponseAutomation | None,
    task_type: str,
    status: str,
    vacancy: Vacancy,
    prompt: str = "",
    response_text: str = "",
    parsed_json: dict[str, Any] | None = None,
    error_reason: str = "",
    vacancy_text: str = "",
    resume_text: str = "",
) -> None:
    if automation is None or automation.analytics is None:
        return
    automation.analytics.ai_assist_event(
        task_type=task_type,
        status=status,
        profile=automation.profile_name,
        vacancy=vacancy,
        prompt=prompt,
        response_text=response_text,
        parsed_json=parsed_json,
        error_reason=error_reason,
        metadata={
            "vacancy_text": vacancy_text,
            "resume_text": resume_text,
            **review_metadata(error_reason, status=status),
        },
    )
