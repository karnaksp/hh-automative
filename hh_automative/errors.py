"""Domain-specific automation errors."""

from __future__ import annotations


class HhAutomativeError(Exception):
    """Base package error."""


class ConfigError(HhAutomativeError):
    """Configuration is missing or invalid."""


class AutomationError(HhAutomativeError):
    """Base Selenium-flow error."""


class LoginFailedError(AutomationError):
    """Login did not complete successfully."""


class SelectorChangedError(AutomationError):
    """Expected page element was not found."""


class ManualActionRequiredError(AutomationError):
    """CAPTCHA or another manual checkpoint blocked automation."""


class AlreadyRespondedError(AutomationError):
    """Vacancy was already processed."""


class ResponseFormUnavailableError(AutomationError):
    """Response form cannot be opened."""


class QuestionsRequiredError(AutomationError):
    """Vacancy requires custom answers before response."""


class ResponseNotConfirmedError(AutomationError):
    """Submit action ran, but hh.ru did not confirm the response."""


class LLMConfigError(ConfigError):
    """LLM provider configuration is missing or invalid."""


class LLMProviderError(AutomationError):
    """LLM provider request failed."""
