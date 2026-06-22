from __future__ import annotations

import importlib


def test_public_modules_import() -> None:
    modules = [
        "main",
        "hh_automative.auth",
        "hh_automative.browser",
        "hh_automative.cli",
        "hh_automative.models",
        "hh_automative.runner",
        "hh_automative.settings",
        "hh_automative.state",
        "utils.browser_utils",
        "utils.search_utils",
        "utils.resume_utils",
    ]

    for module in modules:
        importlib.import_module(module)
