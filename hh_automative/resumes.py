"""Resume selection helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass

from selenium.webdriver.common.by import By

from hh_automative.browser import BotContext, click
from hh_automative.errors import SelectorChangedError
from hh_automative.models import ResumeProfile

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _ResumeOption:
    option_id: str
    element: object
    label: str


def _strip(s: str | None) -> str:
    return (s or "").strip().replace("\n", " ").strip()


def _normalize(text: str) -> str:
    return _strip(text).lower().replace("  ", " ")


def _unique_elements(elements: Iterable[object]) -> list[object]:
    seen: set[int] = set()
    result: list[object] = []
    for element in elements:
        marker = id(element)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(element)
    return result


def _resume_label(context: BotContext, option: object) -> str:
    option_id = str(option.get_attribute("id"))

    labels = context.driver.find_elements(By.XPATH, f"//label[@for='{option_id}']")
    if labels:
        text = labels[0].text
        if text:
            return _strip(text)

    labels = option.find_elements(By.XPATH, "../label")
    if labels:
        text = labels[0].text
        if text:
            return _strip(text)

    parent = option.find_element(By.XPATH, "..")
    label_from_parent = parent.get_attribute("aria-label")
    if label_from_parent:
        return _strip(label_from_parent)

    return option_id


def _collect_resume_candidates(context: BotContext) -> list[_ResumeOption]:
    selectors = [
        "//input[contains(@id, 'resume')]",
        "//input[@name='resumeId']",
        "//label[contains(@for, 'resume')]/..//input",
        "//input[@type='radio']",
    ]

    elements: list[object] = []
    for selector in selectors:
        found = context.driver.find_elements(By.XPATH, selector)
        elements.extend(found)

    result: list[_ResumeOption] = []
    for option in _unique_elements(elements):
        option_type = str(option.get_attribute("type") or "").lower()
        if option_type and option_type not in {"radio", "checkbox"}:
            continue

        option_id = str(option.get_attribute("id") or "").strip()
        if not option_id:
            continue

        label = _resume_label(context, option)
        result.append(_ResumeOption(option_id=option_id, element=option, label=label))

    return result


def _resume_card_label(card: object) -> str:
    title_selectors = [
        ".//*[@data-qa='resume-title']",
        ".//*[contains(@data-qa, 'resume-title')]",
        ".//*[contains(@data-qa, 'resume')]",
    ]
    for selector in title_selectors:
        title_elements = card.find_elements(By.XPATH, selector)
        for title_element in title_elements:
            text = _strip(title_element.text)
            if text:
                return text
    return _strip(card.text)


def _collect_resume_card_candidates(context: BotContext) -> list[_ResumeOption]:
    selectors = [
        "//*[@data-qa='resume-title']/ancestor::*[@role='button'][1]",
        "//*[@data-qa='resume-title']/ancestor::button[1]",
        "//*[@data-qa='resume-detail']/ancestor::*[@role='button'][1]",
        "//form[@id='RESPONSE_MODAL_FORM_ID']//*[@role='button'][.//*[@data-qa='resume-title']]",
        "//form[@id='RESPONSE_MODAL_FORM_ID']//button[.//*[@data-qa='resume-title']]",
    ]

    result: list[_ResumeOption] = []
    elements: list[object] = []
    for selector in selectors:
        elements.extend(context.driver.find_elements(By.XPATH, selector))

    for element in _unique_elements(elements):
        if not element.is_displayed():
            continue
        label = _resume_card_label(element)
        if not label:
            continue
        option_id = (
            _strip(element.get_attribute("id"))
            or _strip(element.get_attribute("data-qa"))
            or _strip(element.get_attribute("name"))
            or label
        )
        result.append(_ResumeOption(option_id=option_id, element=element, label=label))
    return result


def _response_submit_buttons(context: BotContext) -> list[object]:
    selectors = [
        '//button[@data-qa="vacancy-response-submit-popup"]',
        '//button[@data-qa="vacancy-response-submit"]',
        '//button[@type="submit"]',
        '//button[@type="button" and contains(@data-qa, "vacancy-response")]',
        '//button[contains(@data-qa, "response") and contains(@data-qa, "submit")]',
    ]
    elements: list[object] = []
    for selector in selectors:
        elements.extend(context.driver.find_elements(By.XPATH, selector))
    return _unique_elements(elements)


def _visible_elements(elements: list[object]) -> list[object]:
    return [
        element
        for element in elements
        if element.is_displayed() and element.size.get("height", 0) > 0
    ]


def _find_best_resume_match(
    candidates: list[_ResumeOption],
    resume_name: str,
    resume_code: str,
) -> _ResumeOption | None:
    for option in candidates:
        if option.option_id == resume_code:
            return option

    normalized_name = _normalize(resume_name)
    for option in candidates:
        if _normalize(option.label).find(normalized_name) >= 0:
            return option

    for option in candidates:
        if normalized_name in _normalize(option.option_id):
            return option

    if candidates:
        return candidates[0]
    return None


def _resume_matches(option: _ResumeOption, resume_name: str, resume_code: str) -> bool:
    normalized_name = _normalize(resume_name)
    normalized_code = _normalize(resume_code)
    return (
        option.option_id == resume_code
        or normalized_name in _normalize(option.label)
        or (normalized_code and normalized_code in _normalize(option.option_id))
    )


def _format_options_for_error(candidates: list[_ResumeOption]) -> str:
    return ", ".join(f"{option.option_id} ({option.label})" for option in candidates)


def choose_resume(context: BotContext, profile: ResumeProfile, vacancy_title: str) -> tuple[str, str]:
    resume_name, resume_code = profile.choose_resume_code(vacancy_title)
    candidates = _collect_resume_candidates(context)
    if not candidates:
        card_candidates = _collect_resume_card_candidates(context)
        if card_candidates:
            selected_card = _find_best_resume_match(card_candidates, resume_name, resume_code)
            if selected_card is None:
                raise SelectorChangedError(
                    "Could not map visible resume card to configured resume. "
                    f"Available resume cards: {_format_options_for_error(card_candidates)}"
                )

            if len(card_candidates) == 1:
                if _resume_matches(selected_card, resume_name, resume_code):
                    LOGGER.info("Using preselected resume card from hh.ru modal: %s", selected_card.label)
                    return selected_card.label, resume_code

                with suppress(SelectorChangedError):
                    click(context, selected_card.element, f"resume selector {selected_card.label}")

                expanded_candidates = _collect_resume_card_candidates(context)
                if len(expanded_candidates) > 1:
                    expanded_selected = _find_best_resume_match(
                        expanded_candidates, resume_name, resume_code
                    )
                    if expanded_selected is not None:
                        click(context, expanded_selected.element, f"resume {expanded_selected.label}")
                        return expanded_selected.label, resume_code

                if _response_submit_buttons(context):
                    LOGGER.warning(
                        "Visible resume card '%s' does not exactly match configured resume '%s', "
                        "but no alternate selector was exposed; continuing with visible resume.",
                        selected_card.label,
                        resume_name,
                    )
                    return selected_card.label, resume_code
            else:
                click(context, selected_card.element, f"resume {selected_card.label}")
                return selected_card.label, resume_code

        submit_buttons = _response_submit_buttons(context)
        if submit_buttons:
            LOGGER.info(
                "No explicit resume controls detected; assuming resume is preselected and continuing."
            )
            return resume_name, resume_code
        raise SelectorChangedError(
            "No resume selection controls were found on the vacancy response page."
        )

    selected = _find_best_resume_match(candidates, resume_name, resume_code)
    if selected is None:
        raise SelectorChangedError(
            "Could not map resume to a selectable option. "
            f"Available resumes: {_format_options_for_error(candidates)}"
        )

    click(context, selected.element, f"resume {selected.label}")
    return selected.label, selected.option_id
