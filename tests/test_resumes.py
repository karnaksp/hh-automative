from __future__ import annotations

from types import SimpleNamespace

from hh_automative import resumes
from hh_automative.models import ResumeProfile


def test_choose_resume_uses_matching_keyword() -> None:
    profile = ResumeProfile(
        default_resume="Data Scientist",
        resume_codes={
            "python": "resume_python",
            "Data Scientist": "resume_ds",
        },
    )

    name, code = profile.choose_resume_code("Junior Python Developer")

    assert name == "python"
    assert code == "resume_python"


def test_choose_resume_falls_back_to_default() -> None:
    profile = ResumeProfile(
        default_resume="Data Scientist",
        resume_codes={
            "python": "resume_python",
            "Data Scientist": "resume_ds",
        },
    )

    name, code = profile.choose_resume_code("ML Intern")

    assert name == "Data Scientist"
    assert code == "resume_ds"


class _FakeElement:
    def __init__(
        self,
        *,
        text: str = "",
        attrs: dict[str, str] | None = None,
        children_by_xpath: dict[str, list[_FakeElement]] | None = None,
        displayed: bool = True,
    ) -> None:
        self.text = text
        self._attrs = attrs or {}
        self._children_by_xpath = children_by_xpath or {}
        self._displayed = displayed

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    def find_elements(self, by: str, selector: str):  # noqa: ARG002
        return list(self._children_by_xpath.get(selector, []))

    def find_element(self, by: str, selector: str):  # noqa: ARG002
        elements = self.find_elements(by, selector)
        if not elements:
            raise LookupError(selector)
        return elements[0]

    def is_displayed(self) -> bool:
        return self._displayed


class _FakeDriver:
    def __init__(self, mapping: dict[str, list[_FakeElement]]) -> None:
        self._mapping = mapping

    def find_elements(self, by: str, selector: str):  # noqa: ARG002
        return list(self._mapping.get(selector, []))


def test_choose_resume_uses_single_visible_resume_card(monkeypatch) -> None:
    card_title = _FakeElement(text="Data engineer")
    card = _FakeElement(
        text="Data engineer 330000",
        attrs={"role": "button", "data-qa": "resume-card"},
        children_by_xpath={
            ".//*[@data-qa='resume-title']": [card_title],
        },
    )
    submit = _FakeElement(text="Откликнуться", attrs={"data-qa": "vacancy-response-submit-popup"})
    driver = _FakeDriver(
        {
            "//*[@data-qa='resume-title']/ancestor::*[@role='button'][1]": [card],
            '//button[@data-qa="vacancy-response-submit-popup"]': [submit],
        }
    )
    context = SimpleNamespace(driver=driver)
    profile = ResumeProfile(
        default_resume="Data engineer",
        resume_codes={"Data engineer": "resume_data_engineer"},
    )

    clicked: list[str] = []
    monkeypatch.setattr(resumes, "click", lambda context, element, description: clicked.append(description))

    label, code = resumes.choose_resume(context, profile, "Senior Data Engineer")

    assert label == "Data engineer"
    assert code == "resume_data_engineer"
    assert clicked == []


def test_choose_resume_clicks_matching_resume_card_when_multiple(monkeypatch) -> None:
    first_title = _FakeElement(text="Data scientist")
    second_title = _FakeElement(text="Data engineer")
    first = _FakeElement(
        text="Data scientist",
        attrs={"role": "button", "data-qa": "resume-card-scientist"},
        children_by_xpath={".//*[@data-qa='resume-title']": [first_title]},
    )
    second = _FakeElement(
        text="Data engineer",
        attrs={"role": "button", "data-qa": "resume-card-engineer"},
        children_by_xpath={".//*[@data-qa='resume-title']": [second_title]},
    )
    driver = _FakeDriver(
        {
            "//*[@data-qa='resume-title']/ancestor::*[@role='button'][1]": [first, second],
        }
    )
    context = SimpleNamespace(driver=driver)
    profile = ResumeProfile(
        default_resume="Data scientist",
        resume_codes={
            "Data scientist": "resume_ds",
            "Data engineer": "resume_de",
        },
    )

    clicked: list[str] = []
    monkeypatch.setattr(resumes, "click", lambda context, element, description: clicked.append(description))

    label, code = resumes.choose_resume(context, profile, "Lead Data Engineer")

    assert label == "Data engineer"
    assert code == "resume_de"
    assert clicked == ["resume Data engineer"]
