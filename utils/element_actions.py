"""Backward-compatible element action helpers."""

from __future__ import annotations


def set_value_with_event(element, value: str, driver) -> None:
    driver.execute_script(
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
        element,
        value,
    )
