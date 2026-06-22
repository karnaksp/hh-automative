"""Backward-compatible resume helpers."""

from __future__ import annotations

from hh_automative.models import ActionResult, Status
from hh_automative.resumes import choose_resume


def fill_in_cover_letter(_message, _driver, _wait) -> ActionResult:
    return ActionResult(Status.SKIPPED, "Use hh_automative.response.respond_to_vacancy instead.")


def check_cover_letter_popup(*_args, **_kwargs) -> ActionResult:
    return ActionResult(Status.SKIPPED, "Deprecated compatibility helper.")


def answer_questions(*_args, **_kwargs) -> ActionResult:
    return ActionResult(Status.SKIPPED, "Question automation is not implemented.")


__all__ = ["answer_questions", "check_cover_letter_popup", "choose_resume", "fill_in_cover_letter"]
