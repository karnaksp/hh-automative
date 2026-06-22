"""Submit-button heuristics tests."""

from __future__ import annotations

from hh_automative.models import Vacancy
from hh_automative.response import (
    _collect_text_candidates,
    _is_cross_country_confirm_control,
    _is_response_button,
    _is_response_related_control,
    _is_submit_label,
    _open_response_form,
    _page_text_contains_response_success,
    _pick_best_response_button,
    _pick_best_submit_candidate,
    _response_ready_or_redirect,
    _score_submit_candidate,
)


class FakeSubmitButton:
    def __init__(
        self,
        text: str = "",
        attrs: dict[str, str] | None = None,
        enabled: bool = True,
        displayed: bool = True,
    ) -> None:
        self.text = text
        self._attrs = dict(attrs or {})
        self._enabled = enabled
        self._displayed = displayed

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    def is_displayed(self) -> bool:
        return self._displayed

    def is_enabled(self) -> bool:
        return self._enabled

    def find_elements(self, by: str, selector: str):  # noqa: ARG002 - Selenium API compatibility
        return []


class FakeContainer:
    def __init__(self, text: str = "", attrs: dict[str, str] | None = None) -> None:
        self.text = text
        self._attrs = dict(attrs or {})

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)


class FakeDriver:
    def __init__(self, current_url: str, xpath_hits: dict[str, list[object]] | None = None) -> None:
        self.current_url = current_url
        self._xpath_hits = xpath_hits or {}

    def find_elements(self, by: str, selector: str):
        if by != "xpath":
            return []
        return self._xpath_hits.get(selector, [])

    def get(self, url: str) -> None:
        self.current_url = url


class FakeContext:
    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver
        self.settings = type("Settings", (), {"timeout_seconds": 1})()


def test_pick_best_submit_candidate_prefers_labeled_submit_button() -> None:
    form = FakeContainer(
        text="Форма отклика. Заполните сопроводительное письмо.",
        attrs={"data-qa": "vacancy-response-form"},
    )
    buttons = [
        FakeSubmitButton("Подробнее", {"type": "button", "class": "secondary"}),
        FakeSubmitButton(
            "Отправить отклик",
            {"type": "button", "class": "btn btn-submit", "data-qa": "vacancy-response-submit"},
        ),
        FakeSubmitButton("Отмена", {"type": "button", "class": "link"}),
    ]

    chosen = _pick_best_submit_candidate(buttons, [form])

    assert chosen is buttons[1]
    assert _score_submit_candidate(chosen, [form]) > _score_submit_candidate(buttons[0], [form])


def test_pick_best_submit_candidate_prefers_link_with_aria_label() -> None:
    form = FakeContainer(
        text="Сопроводительное письмо",
        attrs={"data-qa": "vacancy-response-form"},
    )
    buttons = [
        FakeSubmitButton("", {"type": "button", "data-qa": "vacancy-action", "aria-label": "Открыть"}),
        FakeSubmitButton("", {"role": "button", "aria-label": "Откликнуться на вакансию"}),
    ]

    chosen = _pick_best_submit_candidate(buttons, [form])
    assert chosen is buttons[1]


def test_pick_best_submit_candidate_prefers_title_signal() -> None:
    form = FakeContainer(
        text="Форма отклика",
        attrs={"data-qa": "vacancy-response-form"},
    )
    buttons = [
        FakeSubmitButton("", {"type": "button", "title": "Подробнее"}),
        FakeSubmitButton("", {"type": "button", "title": "Отправить отклик"}),
    ]

    chosen = _pick_best_submit_candidate(buttons, [form])
    assert chosen is buttons[1]


def test_pick_best_submit_candidate_prefers_submit_anchor() -> None:
    form = FakeContainer(
        text="Отклик",
        attrs={"data-qa": "vacancy-response-form"},
    )
    buttons = [
        FakeSubmitButton("Подробнее", {"role": "button", "class": "muted"}),
        FakeSubmitButton("", {"role": "button", "href": "#", "aria-label": "Перейти"}),
        FakeSubmitButton("", {"role": "button", "href": "#", "aria-label": "Откликнуться на вакансию"}),
    ]

    chosen = _pick_best_submit_candidate(buttons, [form])
    assert chosen is buttons[2]


def test_pick_best_submit_candidate_prefers_response_scope_over_generic_button() -> None:
    form = FakeContainer(
        text="Форма отклика и сопроводительное письмо.",
        attrs={"data-qa": "vacancy-response-form"},
    )
    buttons = [
        FakeSubmitButton("Подробнее", {"type": "button", "class": "btn btn-secondary"}),
        FakeSubmitButton("Отмена", {"type": "button", "class": "btn"}),
        FakeSubmitButton("", {"type": "button", "data-qa": "vacancy-response-submit", "aria-label": "Сохранить"}),
    ]

    chosen = _pick_best_submit_candidate(buttons, [form])
    assert chosen is buttons[2]


def test_submit_label_rejects_cancel_signal() -> None:
    assert not _is_submit_label("Отмена")
    assert not _is_submit_label("Закрыть")


def test_cross_country_confirm_control_detects_primary_button() -> None:
    confirm = FakeSubmitButton("Все равно откликнуться", {"type": "button"})
    cancel = FakeSubmitButton("Отменить", {"type": "button"})

    assert _is_cross_country_confirm_control(confirm) is True
    assert _is_cross_country_confirm_control(cancel) is False


def test_cross_country_confirm_control_detects_aria_label() -> None:
    confirm = FakeSubmitButton("", {"aria-label": "Все равно откликнуться", "role": "button"})

    assert _is_cross_country_confirm_control(confirm) is True


def test_page_text_contains_response_success_detects_confirmed_state() -> None:
    assert _page_text_contains_response_success("Спасибо. Отклик отправлен работодателю.") is True
    assert _page_text_contains_response_success("Вы откликнулись на вакансию") is True
    assert _page_text_contains_response_success("Откликнуться на вакансию") is False


def test_response_ready_or_redirect_detects_questionnaire_page() -> None:
    driver = FakeDriver(
        "https://spb.hh.ru/applicant/vacancy_response?vacancyId=1",
    )

    assert _response_ready_or_redirect(FakeContext(driver)) == "open"


def test_response_ready_or_redirect_detects_standard_response_modal() -> None:
    driver = FakeDriver(
        "https://spb.hh.ru/vacancy/1",
        xpath_hits={'//*[@role="dialog"]//*[contains(text(), "Отклик на вакансию")]': [object()]},
    )

    assert _response_ready_or_redirect(FakeContext(driver)) == "open"


def test_open_response_form_uses_current_vacancy_id_before_button_fallback() -> None:
    driver = FakeDriver("https://hh.ru/vacancy/134453065")
    context = FakeContext(driver)

    _open_response_form(
        context,
        Vacancy(url="https://hh.ru/vacancy/134453065", vacancy_id="134453065"),
    )

    assert driver.current_url == "https://hh.ru/applicant/vacancy_response?vacancyId=134453065"


def test_pick_best_response_button_prefers_explicit_text() -> None:
    buttons = [
        FakeSubmitButton("Подробнее", {"type": "button", "data-qa": "vacancy-response-todo"}),
        FakeSubmitButton("Откликнуться на вакансию", {"type": "button", "data-qa": "vacancy-cta"}),
        FakeSubmitButton("Отмена", {"type": "button", "data-qa": "cancel"}),
    ]

    assert _pick_best_response_button(buttons) is buttons[1]


def test_is_response_button_ignores_cancel() -> None:
    cancel = FakeSubmitButton("Отмена", {"role": "button"})
    assert not _is_response_button(cancel)


def test_collect_text_candidates_contains_multiple_sources() -> None:
    assert "Откликнуться на вакансию" in _collect_text_candidates(FakeSubmitButton(
        "",
        {
            "aria-label": "Откликнуться на вакансию",
            "value": "1",
        },
    ))


def test_response_related_control_detects_vacancy_response_markers() -> None:
    related = FakeSubmitButton(
        "Сохранить",
        {"data-qa": "vacancy-response-send", "name": "send"},
    )
    not_related = FakeSubmitButton("Подробнее", {"data-qa": "vacancy-action", "role": "button"})

    assert _is_response_related_control(related) is True
    assert _is_response_related_control(not_related) is False


def test_submit_score_penalizes_cancel_like_label() -> None:
    form = FakeContainer(text="Форма отклика", attrs={"data-qa": "vacancy-response-form"})
    cancel_button = FakeSubmitButton("Отмена", {"type": "button", "data-qa": "vacancy-response-submit"})
    submit_button = FakeSubmitButton("Отправить отклик", {"type": "button", "data-qa": "vacancy-response-submit"})

    assert _score_submit_candidate(submit_button, [form]) > _score_submit_candidate(cancel_button, [form])


def test_pick_best_submit_candidate_ignores_low_score_if_stronger_exists() -> None:
    form = FakeContainer(text="Форма отклика", attrs={"data-qa": "vacancy-response-form"})
    weak = FakeSubmitButton("Открыть", {"type": "button", "data-qa": "vacancy-response"})
    strong = FakeSubmitButton(
        "Отправить отклик",
        {"type": "submit", "data-qa": "vacancy-response-submit", "aria-label": "Откликнуться"},
    )

    chosen = _pick_best_submit_candidate([weak, strong], [form])

    assert chosen is strong
