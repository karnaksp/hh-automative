"""Helpers for extracting and filling hh.ru response forms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hh_automative.ai_assist import VacancyQuestion
from hh_automative.browser import BotContext


@dataclass(slots=True)
class FormField:
    question: VacancyQuestion
    element: Any
    option_elements: dict[str, Any]


def extract_vacancy_text(context: BotContext) -> str:
    from selenium.webdriver.common.by import By

    _expand_vacancy_description_if_needed(context)

    selectors = [
        '[data-qa="vacancy-description"]',
        '[data-qa="vacancy-description-text"]',
        '[data-qa="vacancy-section"]',
        '[data-qa="vacancy-about"]',
        "main article",
        "main",
        "body",
    ]
    collected: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        elements = context.driver.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            if not element.is_displayed():
                continue
            text = (element.text or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            if len(text) < 30:
                continue
            collected.append(text)
    if collected:
        return "\n\n".join(collected)
    return context.driver.find_element(By.TAG_NAME, "body").text.strip()


def _expand_vacancy_description_if_needed(context: BotContext) -> None:
    from selenium.common.exceptions import WebDriverException

    from hh_automative.browser import click

    expand_selectors = [
        '//button[contains(text(), "Раскрыть")]',
        '//button[contains(text(), "раскрыть")]',
        '//button[contains(text(), "Показать все")]',
        '//button[contains(text(), "показать все")]',
        '//button[contains(text(), "Подробнее")]',
        '//button[contains(text(), "Ещё")]',
        '//button[contains(text(), "ЕЩЕ")]',
        '//a[contains(text(), "Показать")]',
        '//button[contains(@data-qa, "vacancy-description-show") or contains(@data-qa, "vacancy-expand")]',
    ]
    for selector in expand_selectors:
        candidates = context.driver.find_elements("xpath", selector)
        for candidate in candidates:
            try:
                if candidate.is_displayed() and candidate.is_enabled():
                    click(context, candidate, "vacancy description expand")
            except WebDriverException:
                continue


def extract_response_questions(context: BotContext) -> list[FormField]:
    fields: list[FormField] = []
    fields.extend(_extract_text_fields(context))
    fields.extend(_extract_choice_fields(context, "radio"))
    fields.extend(_extract_choice_fields(context, "checkbox"))
    fields.extend(_extract_select_fields(context))
    return _deduplicate_fields(fields)


def extract_cover_letter_field(context: BotContext) -> Any | None:
    from selenium.webdriver.common.by import By

    selectors = [
        'textarea[data-qa*="vacancy-response-letter"]',
        'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
        'textarea[data-qa*="popup-form-letter"]',
        'textarea[data-qa*="cover-letter"]',
        'textarea[name*="letter"]',
        'textarea[name="message"]',
        'textarea[name*="message"]',
        'textarea[aria-label*="сопровод"]',
        'textarea[aria-label*="сообщ"]',
        'textarea[placeholder*="сопровод"]',
        'textarea[aria-label*="сопровод"]',
        'textarea[placeholder*="сообщ"]',
    ]
    for selector in selectors:
        fields = _visible(context.driver.find_elements(By.CSS_SELECTOR, selector))
        if fields:
            return fields[0]

    all_textareas = _visible(
        context.driver.find_elements(
            By.XPATH,
            (
                '//form[@action="/applicant/vacancy_response/edit_ajax"]//textarea'
                ' | //form[contains(@action, "vacancy_response")]//textarea'
                ' | //form[@name="vacancy_response"]//textarea'
            ),
        )
    )
    if all_textareas:
        matching_fields = sorted(
            [(field, _cover_letter_score(field)) for field in all_textareas],
            key=lambda item: item[1],
            reverse=True,
        )
        top_field, top_score = matching_fields[0]
        if top_score > 0:
            return top_field

    # fallback: if only one textarea in response context, prefer it
    form_fields = _visible(
        context.driver.find_elements(
            By.XPATH,
            '//form[contains(@action, "vacancy_response")]//textarea | //form[@name="vacancy_response"]//textarea',
        )
    )
    if len(form_fields) == 1:
        return form_fields[0]
    if all_textareas:
        return all_textareas[0]
    return None


def apply_structured_answers(context: BotContext, fields: list[FormField], payload: dict[str, Any]) -> None:
    answers = payload.get("answers", [])
    for answer in answers:
        field = _match_field(fields, str(answer.get("question_id", "")), str(answer.get("label", "")))
        if field is None:
            continue
        answer_text = str(answer.get("answer_text", "") or "")
        selected_options = [str(option) for option in answer.get("selected_options", [])]
        if field.question.input_type in {"textarea", "text"} and answer_text:
            _set_input_value(context, field.element, answer_text)
        elif field.question.input_type in {"radio", "checkbox"}:
            for option in selected_options:
                option_element = _match_option(field.option_elements, option)
                if option_element is not None and not option_element.is_selected():
                    _click_option(context, option_element)
        elif field.question.input_type == "select":
            _select_option(field.element, selected_options[0] if selected_options else answer_text)


def validate_structured_answers(fields: list[FormField], payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    answers = payload.get("answers", [])
    matched_fields: set[str] = set()
    for index, answer in enumerate(answers, start=1):
        if not isinstance(answer, dict):
            errors.append(f"answer #{index} is not an object")
            continue
        field = _match_field(fields, str(answer.get("question_id", "")), str(answer.get("label", "")))
        if field is None:
            errors.append(f"answer #{index} does not match any extracted question")
            continue
        matched_fields.add(field.question.question_id)
        answer_text = str(answer.get("answer_text", "") or "").strip()
        selected_options = [str(option) for option in answer.get("selected_options", [])]
        if field.question.input_type in {"textarea", "text"} and field.question.required and not answer_text:
            errors.append(f"required text question '{field.question.label}' has empty answer_text")
        if field.question.input_type in {"radio", "select"}:
            if field.question.required and not selected_options and not answer_text:
                errors.append(f"required choice question '{field.question.label}' has no selection")
            values_to_check = selected_options or ([answer_text] if answer_text else [])
            for option in values_to_check:
                if _match_option(field.option_elements, option) is None and not _option_in_labels(
                    field.question.options, option
                ):
                    errors.append(
                        f"question '{field.question.label}' selected unknown option '{option}'"
                    )
        if field.question.input_type == "checkbox":
            for option in selected_options:
                if _match_option(field.option_elements, option) is None and not _option_in_labels(
                    field.question.options, option
                ):
                    errors.append(
                        f"question '{field.question.label}' selected unknown option '{option}'"
                    )

    for field in fields:
        if field.question.required and field.question.question_id not in matched_fields:
            errors.append(f"required question '{field.question.label}' is missing from answers")
    return errors


def find_suspicious_choice_fields(fields: list[FormField]) -> list[str]:
    suspicious: list[str] = []
    for field in fields:
        if field.question.input_type not in {"radio", "checkbox", "select"}:
            continue
        options = [option.strip() for option in field.question.options if option.strip()]
        if not options:
            suspicious.append(f"{field.question.label}: no extracted options")
            continue
        normalized_label = _normalize(field.question.label)
        normalized_options = {_normalize(option) for option in options}
        if len(normalized_options) == 1 and normalized_label in normalized_options:
            suspicious.append(f"{field.question.label}: option duplicates question label")
        if _looks_like_response_suggestion_label(field.question.label, options):
            suspicious.append(f"{field.question.label}: looks like hh.ru response suggestion")
    return suspicious


def _extract_text_fields(context: BotContext) -> list[FormField]:
    from selenium.webdriver.common.by import By

    fields: list[FormField] = []
    elements = context.driver.find_elements(
        By.XPATH,
        '//textarea | //input[not(@type) or @type="text" or @type="email" or @type="number"]',
    )
    for index, element in enumerate(_visible(elements), start=1):
        if _is_cover_letter_field(element):
            continue
        label = _label_for(element) or f"text_{index}"
        input_type = "textarea" if element.tag_name.lower() == "textarea" else "text"
        question_id = _field_id(element, f"{input_type}_{index}")
        fields.append(
            FormField(
                question=VacancyQuestion(
                    question_id=question_id,
                    label=label,
                    input_type=input_type,
                    options=[],
                    required=_is_required(element),
                ),
                element=element,
                option_elements={},
            )
        )
    return fields


def _extract_choice_fields(context: BotContext, input_type: str) -> list[FormField]:
    from selenium.webdriver.common.by import By

    inputs = _visible(context.driver.find_elements(By.XPATH, f'//input[@type="{input_type}"]'))
    groups: dict[str, list[Any]] = {}
    for element in inputs:
        if _is_resume_selector_field(element):
            continue
        if _is_response_suggestion_field(element):
            continue
        group_id = element.get_attribute("name") or element.get_attribute("data-qa") or _label_for(element)
        groups.setdefault(group_id or f"{input_type}_{len(groups) + 1}", []).append(element)

    fields: list[FormField] = []
    for index, (group_id, group_elements) in enumerate(groups.items(), start=1):
        labels = {_option_label(element): element for element in group_elements}
        question_label = _group_label(group_elements[0]) or group_id
        fields.append(
            FormField(
                question=VacancyQuestion(
                    question_id=str(group_id or f"{input_type}_{index}"),
                    label=question_label,
                    input_type=input_type,
                    options=[label for label in labels if label],
                    required=any(_is_required(element) for element in group_elements),
                ),
                element=group_elements[0],
                option_elements=labels,
            )
        )
    return fields


def _extract_select_fields(context: BotContext) -> list[FormField]:
    from selenium.webdriver.common.by import By

    fields: list[FormField] = []
    for index, element in enumerate(_visible(context.driver.find_elements(By.TAG_NAME, "select")), start=1):
        options = [
            option.text.strip()
            for option in element.find_elements(By.TAG_NAME, "option")
            if option.text.strip()
        ]
        fields.append(
            FormField(
                question=VacancyQuestion(
                    question_id=_field_id(element, f"select_{index}"),
                    label=_label_for(element) or f"select_{index}",
                    input_type="select",
                    options=options,
                    required=_is_required(element),
                ),
                element=element,
                option_elements={},
            )
        )
    return fields


def _deduplicate_fields(fields: list[FormField]) -> list[FormField]:
    seen: set[str] = set()
    result: list[FormField] = []
    for field in fields:
        key = f"{field.question.input_type}:{field.question.question_id}:{field.question.label}"
        if key in seen:
            continue
        seen.add(key)
        result.append(field)
    return result


def _visible(elements: list[Any]) -> list[Any]:
    return [
        element
        for element in elements
        if element.is_displayed() and element.size.get("height", 0) > 0
    ]


def _field_id(element: Any, fallback: str) -> str:
    return (
        element.get_attribute("id")
        or element.get_attribute("name")
        or element.get_attribute("data-qa")
        or fallback
    )


def _is_required(element: Any) -> bool:
    return bool(element.get_attribute("required") or element.get_attribute("aria-required") == "true")


def _is_cover_letter_field(element: Any) -> bool:
    values = [
        element.get_attribute("id"),
        element.get_attribute("name"),
        element.get_attribute("data-qa"),
        element.get_attribute("placeholder"),
        element.get_attribute("aria-label"),
        _label_for(element),
        _nearby_text(element),
    ]
    return _looks_like_cover_letter(" ".join(value for value in values if value))


def _is_resume_selector_field(element: Any) -> bool:
    values = [
        element.get_attribute("id"),
        element.get_attribute("name"),
        element.get_attribute("data-qa"),
        element.get_attribute("value"),
        _label_for(element),
        _nearby_text(element),
        _ancestor_signature(element),
    ]
    normalized = _normalize(" ".join(value for value in values if value))
    markers = (
        "resume-title",
        "resume-detail",
        "magritte-select-option",
        "data-resume-hash",
        "resume",
        "резюме",
    )
    if any(marker in normalized for marker in markers):
        return True
    value = _normalize(element.get_attribute("value") or "")
    return bool(len(value) >= 24 and value.isalnum())


def _is_response_suggestion_field(element: Any) -> bool:
    try:
        element.find_element("xpath", "./ancestor::*[contains(@class, 'vacancy-response-suggest')][1]")
        return True
    except Exception:
        return False


def _looks_like_response_suggestion_label(label: str, options: list[str]) -> bool:
    normalized = _normalize(" ".join([label, *options]))
    markers = (
        "где располагается место работы",
        "где работа",
        "зарплата",
        "вакансия открыта",
        "актуальна ли вакансия",
        "актуальна вакансия",
    )
    return any(marker in normalized for marker in markers)


def _looks_like_cover_letter(value: str) -> bool:
    normalized = _normalize(value)
    markers = [
        "cover letter",
        "cover-letter",
        "vacancy response letter",
        "vacancy-response-letter",
        "сопровод",
        "сопроводительное письмо",
        "сообщение",
        "текст сопровод",
        "cover message",
    ]
    return any(marker in normalized for marker in markers)


def _cover_letter_score(element: Any) -> int:
    value = " ".join(
        _safe_field_value(part)
        for part in (
            element.get_attribute("id"),
            element.get_attribute("name"),
            element.get_attribute("data-qa"),
            element.get_attribute("placeholder"),
            element.get_attribute("aria-label"),
            _label_for(element),
            _nearby_text(element),
            element.get_attribute("value"),
            element.text,
        )
        if part
    )
    score = 0
    normalized = _normalize(value)
    positive = (
        "сопровод",
        "сообщ",
        "cover letter",
        "message",
        "vacancy-response-letter",
        "vacancy-response-message",
    )
    for marker in positive:
        if marker in normalized:
            score += 50
    if element.tag_name.lower() == "textarea":
        score += 3
    return score


def _safe_field_value(value: str) -> str:
    return value if value is not None else ""


def _label_for(element: Any) -> str:
    from selenium.webdriver.common.by import By

    element_id = element.get_attribute("id")
    if element_id:
        labels = element.parent.find_elements(By.XPATH, f'//label[@for="{element_id}"]')
        if labels:
            return labels[0].text.strip()
    aria_label = element.get_attribute("aria-label")
    if aria_label:
        return aria_label.strip()
    placeholder = element.get_attribute("placeholder")
    if placeholder:
        return placeholder.strip()
    return _nearby_text(element)


def _option_label(element: Any) -> str:
    from selenium.webdriver.common.by import By

    element_id = element.get_attribute("id")
    if element_id:
        labels = element.parent.find_elements(By.XPATH, f'//label[@for="{element_id}"]')
        if labels and labels[0].text.strip():
            return labels[0].text.strip()
    return _nearby_text(element)


def _group_label(element: Any) -> str:
    try:
        container = element.find_element("xpath", "./ancestor::*[self::fieldset or @role='group'][1]")
        return container.text.strip().splitlines()[0]
    except Exception:
        return _label_for(element)


def _nearby_text(element: Any) -> str:
    try:
        parent_text = element.find_element("xpath", "..").text.strip()
    except Exception:
        return ""
    return parent_text.splitlines()[0] if parent_text else ""


def _ancestor_signature(element: Any) -> str:
    try:
        container = element.find_element(
            "xpath",
            "./ancestor::*[@data-qa or @data-magritte-select-option or @role][1]",
        )
    except Exception:
        return ""
    values = [
        container.get_attribute("data-qa"),
        container.get_attribute("data-magritte-select-option"),
        container.get_attribute("role"),
        container.text,
    ]
    return " ".join(value for value in values if value)


def _match_field(fields: list[FormField], question_id: str, label: str) -> FormField | None:
    normalized_label = _normalize(label)
    for field in fields:
        if question_id and question_id == field.question.question_id:
            return field
        if normalized_label and normalized_label == _normalize(field.question.label):
            return field
    return None


def _match_option(option_elements: dict[str, Any], option: str) -> Any | None:
    normalized = _normalize(option)
    for label, element in option_elements.items():
        normalized_label = _normalize(label)
        if _labels_match(normalized_label, normalized):
            return element
    return None


def _option_in_labels(labels: list[str], option: str) -> bool:
    normalized = _normalize(option)
    for label in labels:
        normalized_label = _normalize(label)
        if _labels_match(normalized_label, normalized):
            return True
    return False


def _labels_match(normalized_label: str, normalized_value: str) -> bool:
    if not normalized_label or not normalized_value:
        return False
    if normalized_label == normalized_value:
        return True
    if len(normalized_label) < 4 or len(normalized_value) < 4:
        return False
    return normalized_value in normalized_label or normalized_label in normalized_value


def _set_input_value(context: BotContext, element: Any, value: str) -> None:
    context.driver.execute_script(
        """
        const element = arguments[0];
        const value = arguments[1];
        element.scrollIntoView({ block: "center", inline: "nearest" });
        if (typeof element.focus === "function") {
            element.focus({ preventScroll: true });
        }
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


def _click_option(context: BotContext, element: Any) -> None:
    from selenium.common.exceptions import WebDriverException

    try:
        context.driver.execute_script("arguments[0].click();", element)
        return
    except WebDriverException:
        pass

    label = _clickable_label_for(element)
    if label is not None:
        context.driver.execute_script("arguments[0].click();", label)
        return
    parent = element.find_element("xpath", "..")
    context.driver.execute_script("arguments[0].click();", parent)


def _clickable_label_for(element: Any) -> Any | None:
    from selenium.webdriver.common.by import By

    element_id = element.get_attribute("id")
    if element_id:
        labels = element.parent.find_elements(By.XPATH, f'//label[@for="{element_id}"]')
        if labels:
            return labels[0]
    try:
        return element.find_element("xpath", "./ancestor::label[1]")
    except Exception:
        return None


def _select_option(element: Any, label: str) -> None:
    from selenium.webdriver.support.select import Select

    select = Select(element)
    normalized = _normalize(label)
    for option in select.options:
        normalized_option = _normalize(option.text)
        if normalized_option == normalized or (
            normalized and (normalized in normalized_option or normalized_option in normalized)
        ):
            select.select_by_visible_text(option.text)
            return


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
