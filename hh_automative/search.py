"""Search and vacancy discovery."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from hh_automative.browser import BotContext, click, wait_for
from hh_automative.models import SearchProfile, Vacancy

LOGGER = logging.getLogger(__name__)


def open_search(context: BotContext, profile: SearchProfile) -> None:
    if profile.advanced_search_url:
        context.driver.get(profile.advanced_search_url)
        return
    params = {
        "text": profile.query,
        "excluded_text": profile.exclude,
        "enable_snippets": "false",
    }
    if profile.min_salary:
        params["salary"] = profile.min_salary
    if profile.only_with_salary:
        params["only_with_salary"] = "true"
    context.driver.get(f"https://hh.ru/search/vacancy?{urlencode(params)}")


def collect_vacancies(context: BotContext, limit: int) -> list[Vacancy]:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    vacancies: list[Vacancy] = []
    seen: set[str] = set()
    pages_checked = 0
    max_pages = max(3, min(20, (limit // 20) + 3))

    while len(vacancies) < limit and pages_checked < max_pages:
        pages_checked += 1
        wait_for(
            context,
            EC.presence_of_element_located((By.XPATH, '//a[contains(@href, "/vacancy/")]')),
            "vacancy links",
        )
        links = _vacancy_link_snapshots(context)
        page_added = 0
        for link in links:
            href = str(link.get("href") or "")
            normalized = _normalize_vacancy_url(href)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            title = str(link.get("title") or "").strip()
            vacancies.append(
                Vacancy(url=normalized, vacancy_id=_vacancy_id(normalized), title=title)
            )
            page_added += 1
            if len(vacancies) >= limit:
                break
        LOGGER.info("Collected %s vacancy links from current page.", page_added)
        if len(vacancies) >= limit:
            break
        if not go_next_page(context):
            break
    LOGGER.info("Collected %s vacancy links.", len(vacancies))
    return vacancies


def _vacancy_link_snapshots(context: BotContext) -> list[dict[str, str]]:
    snapshots = context.driver.execute_script(
        """
        return Array.from(document.querySelectorAll('a[href*="/vacancy/"]')).map((link) => ({
            href: link.href || "",
            title: (link.innerText || link.textContent || "").trim(),
        }));
        """
    )
    if not isinstance(snapshots, list):
        return []
    result: list[dict[str, str]] = []
    for item in snapshots:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "href": str(item.get("href") or ""),
                "title": str(item.get("title") or ""),
            }
        )
    return result


def open_vacancy(context: BotContext, vacancy: Vacancy) -> Vacancy:
    context.driver.get(vacancy.url)
    title = _text_or_default(context, '//h1[@data-qa="vacancy-title"]', vacancy.title)
    company = _text_or_default(
        context,
        '//a[@data-qa="vacancy-company-name"] | //*[@data-qa="vacancy-company-name"]',
        "",
    )
    return Vacancy(url=vacancy.url, vacancy_id=vacancy.vacancy_id, title=title, company=company)


def go_next_page(context: BotContext) -> bool:
    from selenium.webdriver.common.by import By

    next_buttons = context.driver.find_elements(By.XPATH, '//a[@data-qa="pager-next"]')
    if not next_buttons:
        next_href = _next_pager_page_href(
            context.driver.current_url,
            [
                link.get_attribute("href") or ""
                for link in context.driver.find_elements(By.XPATH, '//a[@data-qa="pager-page"]')
            ],
        )
        if not next_href:
            return False
        context.driver.get(next_href)
        return True
    click(context, next_buttons[0], "next search page")
    return True


def _next_pager_page_href(current_url: str, hrefs: list[str]) -> str:
    current_page = _page_number(current_url)
    expected_next = current_page + 1
    candidates: list[tuple[int, str]] = []
    for href in hrefs:
        page = _page_number(href)
        if page >= expected_next:
            candidates.append((page, href))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _text_or_default(context: BotContext, xpath: str, default: str) -> str:
    from selenium.webdriver.common.by import By

    elements = context.driver.find_elements(By.XPATH, xpath)
    if not elements:
        return default
    return elements[0].text.strip() or default


def _normalize_vacancy_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if "/vacancy/" not in parsed.path:
        return ""
    vacancy_id = _vacancy_id(url)
    if not vacancy_id.isdigit():
        return ""
    query = parse_qs(parsed.query)
    clean_query = urlencode({"from": query["from"][0]}) if "from" in query else ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", clean_query, ""))


def _vacancy_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1]


def _page_number(url: str) -> int:
    page_values = parse_qs(urlparse(url).query).get("page", ["0"])
    try:
        return int(page_values[0])
    except (TypeError, ValueError):
        return 0
