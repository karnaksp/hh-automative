"""Sync resume identifiers from hh.ru user page to local config."""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hh_automative.auth import login_or_restore_session
from hh_automative.browser import BotContext, close_context, create_context
from hh_automative.errors import ConfigError
from hh_automative.settings import Settings

LOGGER = logging.getLogger(__name__)

RESUME_CODE_RE = re.compile(r"(?:resume_)?([a-f0-9]{20,})")
RESUME_PAGE_URL_TEMPLATE = "https://hh.ru/resume/{code}"
RESUME_TEXT_CACHE_FILE = "index.json"
DEFAULT_RESUME_SYNC_PAGE_LOAD_TIMEOUT_SECONDS = 20
RESUME_TEXT_SELECTORS = [
    "[data-qa='resume-position-card']",
    "[data-qa='resume-about-card']",
    "[data-qa='resume-list-card-education']",
    "[data-qa='resume-list-card-experience']",
    "[data-qa='resume-list-card-certificate']",
    "[data-qa='resume-list-card-additionalEducation']",
    "[data-qa='resume-list-card-recommendation']",
    "[data-qa='resume-contact-email']",
    "[data-qa='resume-contacts-phone']",
    "[data-qa='resume-position-field-employmentForms']",
    "[data-qa='resume-position-field-workFormats']",
    "[data-qa='resume-position-field-travelTime']",
    "[data-qa='resume-position-field-businessTripReadiness']",
    "[data-qa='resume-visibility-card']",
    "[data-qa^='resume-list-card-'][data-qa*='-item-']",
    "[data-qa^='resume-list-card-item-']",
]
RESUME_TEXT_NOISE_SELECTORS = [
    "[data-qa='resume-avatar']",
    "[data-qa^='resume-edit-button']",
    "[data-qa='resume-visibility-card'] [aria-label='Удалить из избранного']",
]


@dataclass(slots=True)
class ResumeItem:
    name: str
    code: str


def sync_resume_config() -> dict[str, object]:
    settings = Settings.load()
    settings.validate_for_browser(require_credentials=True)
    settings.ensure_dirs()
    context = create_context(settings)
    try:
        login_or_restore_session(context)
        context.driver.get("https://hh.ru/applicant/resumes")

        resumes = _collect_resumes(context)
        if not resumes:
            raise ConfigError(
                "No resumes detected on hh.ru/applicant/resumes. "
                "Run `python -m hh_automative login --manual` first and retry."
            )

        # keep deterministic, but leave manual control for key names if same title repeats
        deduped: dict[str, str] = {}
        for item in resumes:
            deduped.setdefault(item.name, item.code)

        # choose a stable default resume in deterministic order
        default_name = next(iter(deduped))
        payload = {
            "default_resume": default_name,
            "resume_codes": deduped,
        }

        path = settings.resumes_path
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        sync_result = sync_resume_texts(context, deduped, settings.resume_text_cache_dir)
        return {
            "status": "updated",
            "path": str(path),
            "default_resume": default_name,
            "count": len(deduped),
            "resume_texts": {
                "path": str(settings.resume_text_cache_dir),
                "synced": sync_result["synced"],
                "saved": sync_result["saved"],
            },
            "resume_codes": deduped,
        }
    finally:
        close_context(context)


def sync_resume_texts(
    context: BotContext,
    resume_codes: dict[str, str],
    cache_dir: Path,
) -> dict[str, object]:
    """Download and persist textual copies of all known resumes to local cache."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    synced: dict[str, str] = {}
    saved: list[str] = []
    successful_sync_count = 0

    # ensure we are authorized on the resumes section once, then open each resume once
    context.driver.get("https://hh.ru/applicant/resumes")
    for name, code in resume_codes.items():
        cache_file = cache_dir / f"{_safe_filename(code)}.txt"
        try:
            text = _extract_resume_text_from_page(context, code)
            cache_file.write_text(text, encoding="utf-8")
            synced[code] = text
            saved.append(code)
            if text:
                successful_sync_count += 1
        except Exception as exc:  # noqa: BLE001 - keep one-run cache robust
            synced[code] = ""
            # keep the mapping so fallback remains transparent
            cache_file.write_text("", encoding="utf-8")
            # optional: diagnostics are handled by caller if needed
            LOGGER.warning("Could not sync resume '%s' (%s): %s", name, code, exc)

    index = {
        "updated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "resumes": {
            code: {
                "name": resume_name,
                "path": str(cache_dir / f"{_safe_filename(code)}.txt"),
                "text_length": len(text),
                "synced": bool(text),
            }
            for resume_name, code in resume_codes.items()
            for text in [synced.get(code, "")]
        },
    }
    (cache_dir / RESUME_TEXT_CACHE_FILE).write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "synced": successful_sync_count,
        "saved": saved,
        "texts": synced,
        "index_path": str(cache_dir / RESUME_TEXT_CACHE_FILE),
    }


def refresh_resume_text_cache(
    context: BotContext,
    resume_codes: dict[str, str],
    cache_dir: Path,
    *,
    page_load_timeout_seconds: int = DEFAULT_RESUME_SYNC_PAGE_LOAD_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Best-effort refresh of cached HH resume texts without blocking the whole run for long."""

    try:
        context.driver.set_page_load_timeout(page_load_timeout_seconds)
    except Exception:  # noqa: BLE001 - driver timeout tuning must not break automation
        LOGGER.debug("Could not set temporary page-load timeout for resume sync.")
    try:
        return sync_resume_texts(context, resume_codes, cache_dir)
    finally:
        try:
            context.driver.set_page_load_timeout(300)
        except Exception:  # noqa: BLE001 - restore is best-effort only
            LOGGER.debug("Could not restore page-load timeout after resume sync.")


def load_cached_resume_text(cache_dir: Path, resume_code: str) -> str:
    """Read cached resume text for one resume code."""

    cache_file = cache_dir / f"{_safe_filename(_normalize_resume_code(resume_code))}.txt"
    if not cache_file.exists():
        return ""
    return cache_file.read_text(encoding="utf-8").strip()


def _collect_resumes(context: BotContext) -> list[ResumeItem]:
    from selenium.webdriver.common.by import By

    context.driver.implicitly_wait(1)
    found: list[ResumeItem] = []

    seen_codes: set[str] = set()

    # 1) direct links
    links = context.driver.find_elements(By.XPATH, "//a[contains(@href,'/applicant/resume')]")
    for link in links:
        href = (link.get_attribute("href") or "").strip()
        code = _extract_code(href)
        if not code:
            continue
        name = _clean_text(link.text)
        if not name:
            continue
        if code in seen_codes:
            continue
        seen_codes.add(code)
        found.append(ResumeItem(name=name, code=code))

    # 2) radio inputs from any embedded selector on this page
    inputs = context.driver.find_elements(By.XPATH, "//input[contains(@id,'resume_')]")
    for input_item in inputs:
        code = (input_item.get_attribute("id") or "").strip()
        if not code or code in seen_codes:
            continue
        label = _label_for_input(context, input_item)
        if not label:
            continue
        seen_codes.add(code)
        found.append(ResumeItem(name=label, code=code))

    # 3) parse inline scripts/state payloads as fallback
    scripts = context.driver.find_elements(By.XPATH, "//script")
    for script in scripts:
        text = script.get_attribute("text") or ""
        for code in set(RESUME_CODE_RE.findall(text)):
            if code in seen_codes:
                continue
            title = _extract_title_around(text, code)
            if not title:
                title = code
            seen_codes.add(code)
            found.append(ResumeItem(name=title, code=code))

    return found


def _extract_resume_text_from_page(context: BotContext, resume_code: str) -> str:
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By

    code = _normalize_resume_code(resume_code)
    try:
        context.driver.get(RESUME_PAGE_URL_TEMPLATE.format(code=code))
    except TimeoutException:
        LOGGER.warning("Resume page %s timed out; continuing with partially loaded DOM.", code)
        with suppress(Exception):
            context.driver.execute_script("window.stop();")

    collected = _collect_resume_text_blocks(context, RESUME_TEXT_SELECTORS)
    if collected:
        return "\n\n".join(collected)

    # fallback to whole body text for protected/compact layouts
    body = context.driver.find_element(By.TAG_NAME, "body").text
    return _extract_plain_body_text(body)


def _collect_resume_text_blocks(context: BotContext, selectors: list[str]) -> list[str]:
    from selenium.webdriver.common.by import By

    collected: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        elements = context.driver.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            text = _normalize_whitespace(element.text or "")
            if not text or text in seen:
                continue
            if len(text) < 30:
                continue
            if not element.is_displayed():
                continue
            if _is_noise_element(element):
                continue
            seen.add(text)
            collected.append(text)
    return collected


def _extract_plain_body_text(raw_body: str) -> str:
    normalized = _normalize_whitespace(raw_body)
    if not normalized:
        return ""

    # remove obvious header/footer noise to avoid saving mostly site chrome
    # based on frequently repeated HH.ru public-page wording.
    noise_patterns = [
        "Создать резюме",
        "Разместить резюме",
        "HeadHunter",
        "Подписывайтесь на наши сообщества",
        "Наши вакансии",
        "Правила использования файлов cookie",
    ]
    for marker in noise_patterns:
        normalized = normalized.replace(marker + " ", "")
        normalized = normalized.replace(" " + marker, "")

    if len(normalized) < 60:
        return ""
    return normalized


def _is_noise_element(element: object) -> bool:
    try:
        data_qa = str(element.get_attribute("data-qa") or "")
    except Exception:
        data_qa = ""
    if not data_qa:
        return False

    if data_qa in {"resume-avatar", "resume-serp_resume-item-content"}:
        return True
    if data_qa.startswith("resume-edit-button"):
        return True
    if "contact" in data_qa and "edit" in data_qa:
        return True
    if "visibility" in data_qa and "card" in data_qa:
        return len((element.text or "").strip()) <= 3
    return False


def _safe_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")


def _normalize_whitespace(value: str) -> str:
    return " ".join((value or "").split())


def _extract_code(text: str) -> str:
    match = RESUME_CODE_RE.search(text)
    if not match:
        return ""
    return _normalize_resume_code(match.group(1))


def _normalize_resume_code(raw_code: str) -> str:
    code = (raw_code or "").strip()
    return code.removeprefix("resume_")


def _extract_title_around(text: str, code: str) -> str:
    marker = text.find(code)
    if marker == -1:
        marker = text.find(f"resume_{code}")
    if marker == -1:
        return ""
    start = max(0, marker - 160)
    end = min(len(text), marker + 160)
    snippet = text[start:end]
    for line in re.split(r"[\r\n,;]", snippet):
        cleaned = _clean_text(line)
        if cleaned and not cleaned.startswith("{") and not cleaned.startswith("\""):
            return cleaned[:160]
    return code


def _label_for_input(context: BotContext, element: object) -> str:
    from selenium.webdriver.common.by import By

    option_id = str(element.get_attribute("id") or "").strip()
    if not option_id:
        return ""
    labels = context.driver.find_elements(By.XPATH, f"//label[@for='{option_id}']")
    if labels:
        text = _clean_text(labels[0].text)
        if text:
            return text
    parents = element.find_elements(By.XPATH, "../..")
    if parents:
        parent_text = _clean_text(parents[0].text)
        if parent_text:
            return parent_text
    return ""


def _clean_text(text: str) -> str:
    return (text or "").strip().replace("\n", " ").replace("\r", " ").strip()
