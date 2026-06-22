from __future__ import annotations

from dataclasses import dataclass

from hh_automative.ai_assist import VacancyQuestion
from hh_automative.hh_form import (
    FormField,
    _is_response_suggestion_field,
    _is_resume_selector_field,
    _looks_like_cover_letter,
    _match_field,
    _match_option,
    _set_input_value,
    extract_cover_letter_field,
    find_suspicious_choice_fields,
    validate_structured_answers,
)


@dataclass
class FakeElement:
    label: str

    def __post_init__(self) -> None:
        self.attrs: dict[str, str] = {}
        self.parent = self

    def get_attribute(self, name: str) -> str:
        return self.attrs.get(name, "")

    def find_element(self, by: str, value: str) -> FakeElement:
        if value == "..":
            return FakeElement(self.label)
        raise Exception("not found")

    @property
    def text(self) -> str:
        return self.label

    @property
    def size(self) -> dict[str, int]:
        return {"height": 100}

    def is_displayed(self) -> bool:
        return True


def test_match_field_by_question_id() -> None:
    field = FormField(
        question=VacancyQuestion("q1", "Опыт SQL", "textarea", []),
        element=FakeElement("field"),
        option_elements={},
    )

    assert _match_field([field], "q1", "") is field


def test_match_field_by_normalized_label() -> None:
    field = FormField(
        question=VacancyQuestion("q1", "  Опыт   SQL ", "textarea", []),
        element=FakeElement("field"),
        option_elements={},
    )

    assert _match_field([field], "", "опыт sql") is field


def test_match_option_by_normalized_label() -> None:
    yes = FakeElement("yes")
    options = {"  Да, есть опыт ": yes}

    assert _match_option(options, "да, есть опыт") is yes


def test_match_option_by_partial_label() -> None:
    remote = FakeElement("remote")
    options = {"Да, могу работать полностью удаленно": remote}

    assert _match_option(options, "полностью удаленно") is remote


def test_cover_letter_marker_detection() -> None:
    assert _looks_like_cover_letter("textarea vacancy-response-letter-toggle")
    assert _looks_like_cover_letter("Сопроводительное письмо работодателю")
    assert not _looks_like_cover_letter("Есть ли опыт SQL?")


def test_extract_cover_letter_field_detects_popup_form_letter_input() -> None:
    textarea = FakeElement("Сопроводительное письмо")
    textarea.attrs = {"data-qa": "vacancy-response-popup-form-letter-input"}

    class FakeDriver:
        def find_elements(self, by: str, selector: str):  # noqa: ARG002
            if by == "css selector" and selector == 'textarea[data-qa="vacancy-response-popup-form-letter-input"]':
                return [textarea]
            return []

    class FakeContext:
        driver = FakeDriver()

    assert extract_cover_letter_field(FakeContext()) is textarea


def test_resume_selector_field_detection_by_resume_markers() -> None:
    element = FakeElement("Data engineer")
    element.attrs = {
        "value": "667cbeb9ff0ce6d6500039ed1f413653306275",
        "data-qa": "radio",
    }
    element.find_element = lambda by, value: FakeAncestor("magritte-select-option-667c", "option", "Data engineer")

    assert _is_resume_selector_field(element) is True


def test_resume_selector_field_detection_does_not_hide_regular_question() -> None:
    element = FakeElement("Есть ли опыт SQL?")
    element.attrs = {
        "name": "question_1",
        "value": "yes",
        "data-qa": "question-answer",
    }
    element.find_element = lambda by, value: FakeAncestor("questionnaire-item", "group", "Есть ли опыт SQL?")

    assert _is_resume_selector_field(element) is False


def test_response_suggestion_field_is_not_questionnaire_answer() -> None:
    element = FakeElement("Где располагается место работы?")
    element.attrs = {"type": "checkbox"}

    def find_element(by: str, value: str) -> FakeElement:  # noqa: ARG001
        if "vacancy-response-suggest" in value:
            return FakeElement("suggestion")
        raise Exception("not found")

    element.find_element = find_element

    assert _is_response_suggestion_field(element) is True


def test_validate_structured_answers_requires_required_questions() -> None:
    field = FormField(
        question=VacancyQuestion("q1", "Опыт SQL", "textarea", [], required=True),
        element=FakeElement("field"),
        option_elements={},
    )

    errors = validate_structured_answers([field], {"answers": []})

    assert errors == ["required question 'Опыт SQL' is missing from answers"]


def test_validate_structured_answers_rejects_unknown_choice() -> None:
    field = FormField(
        question=VacancyQuestion("q1", "Есть опыт SQL?", "radio", ["Да", "Нет"], required=True),
        element=FakeElement("field"),
        option_elements={"Да": FakeElement("yes"), "Нет": FakeElement("no")},
    )

    errors = validate_structured_answers(
        [field],
        {"answers": [{"question_id": "q1", "answer_text": "", "selected_options": ["Иногда"]}]},
    )

    assert errors == ["question 'Есть опыт SQL?' selected unknown option 'Иногда'"]


def test_validate_structured_answers_accepts_partial_choice_match() -> None:
    field = FormField(
        question=VacancyQuestion(
            "q1",
            "Формат работы",
            "radio",
            ["Да, могу работать полностью удаленно", "Нет"],
            required=True,
        ),
        element=FakeElement("field"),
        option_elements={"Да, могу работать полностью удаленно": FakeElement("remote")},
    )

    errors = validate_structured_answers(
        [field],
        {"answers": [{"question_id": "q1", "answer_text": "", "selected_options": ["удаленно"]}]},
    )

    assert errors == []


def test_set_input_value_uses_script_without_element_click() -> None:
    element = FakeElement("field")

    def click() -> None:
        raise AssertionError("direct element click should not be required")

    element.click = click

    class FakeDriver:
        def __init__(self) -> None:
            self.calls: list[tuple[object, str]] = []

        def execute_script(self, script: str, target: object, value: str) -> None:
            self.calls.append((target, value))

    class FakeContext:
        def __init__(self) -> None:
            self.driver = FakeDriver()

    context = FakeContext()
    _set_input_value(context, element, "answer")

    assert context.driver.calls == [(element, "answer")]


def test_find_suspicious_choice_fields_blocks_empty_options() -> None:
    field = FormField(
        question=VacancyQuestion("q1", "Есть ли опыт SQL?", "radio", [], required=True),
        element=FakeElement("field"),
        option_elements={},
    )

    suspicious = find_suspicious_choice_fields([field])

    assert suspicious == ["Есть ли опыт SQL?: no extracted options"]


def test_find_suspicious_choice_fields_detects_hh_suggestion_labels() -> None:
    field = FormField(
        question=VacancyQuestion(
            "q1",
            "Где располагается место работы?",
            "checkbox",
            ["Где располагается место работы?"],
            required=False,
        ),
        element=FakeElement("field"),
        option_elements={},
    )

    suspicious = find_suspicious_choice_fields([field])

    assert suspicious


@dataclass
class FakeAncestor:
    data_qa: str
    role: str
    label: str

    def get_attribute(self, name: str) -> str:
        values = {
            "data-qa": self.data_qa,
            "data-magritte-select-option": self.data_qa if "magritte-select-option" in self.data_qa else "",
            "role": self.role,
        }
        return values.get(name, "")

    @property
    def text(self) -> str:
        return self.label
