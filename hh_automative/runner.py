"""High-level automation runner."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from hh_automative.ai_assist import load_resume_text
from hh_automative.analytics import DuckDBAnalytics, new_run_id
from hh_automative.auth import login_or_restore_session, wait_for_manual_login
from hh_automative.browser import capture_diagnostics, close_context, create_context
from hh_automative.error_taxonomy import review_metadata
from hh_automative.errors import AutomationError
from hh_automative.llm import create_llm_client
from hh_automative.models import RunStats, Status, Vacancy
from hh_automative.relevance import is_relevant_data_engineer_title
from hh_automative.response import ResponseAutomation, respond_to_vacancy
from hh_automative.resume_sync import load_cached_resume_text, refresh_resume_text_cache
from hh_automative.search import collect_vacancies, open_search, open_vacancy
from hh_automative.settings import Settings, load_resume_profile, load_search_profile
from hh_automative.state import StateStore

LOGGER = logging.getLogger(__name__)


def run(
    profile_name: str | None,
    limit: int | None,
    dry_run: bool | None,
    *,
    target_new: bool = False,
    ignore_dry_run_history: bool = True,
) -> RunStats:
    settings = Settings.load()
    settings.validate_for_browser(require_credentials=True)
    settings.ensure_dirs()
    search_profile = load_search_profile(settings, profile_name)
    resume_profile = load_resume_profile(settings)
    effective_limit = limit or settings.default_limit
    context = create_context(settings)
    llm_client = create_llm_client(settings)
    effective_dry_run = settings.dry_run if dry_run is None else dry_run
    store = StateStore(settings.state_db_path)
    analytics = DuckDBAnalytics(settings.analytics_db_path, new_run_id())
    resume_fallback_text = load_resume_text(settings.resume_text_path)
    resume_texts_by_code: dict[str, str] = {}
    response_automation = ResponseAutomation(
        llm=llm_client,
        analytics=analytics,
        profile_name=search_profile.name,
    )
    stats = RunStats()
    try:
        analytics.run_started(search_profile.name, effective_limit, effective_dry_run)
        analytics.event(
            "login_started",
            status="started",
            profile=search_profile.name,
        )
        login_or_restore_session(context)
        analytics.event(
            "login_finished",
            status="success",
            profile=search_profile.name,
        )
        sync_result = refresh_resume_text_cache(
            context,
            resume_profile.resume_codes,
            settings.resume_text_cache_dir,
        )
        analytics.event(
            "resume_texts_synced",
            status="success",
            profile=search_profile.name,
            metadata={
                "synced": sync_result["synced"],
                "saved": len(sync_result["saved"]),
                "cache_dir": str(settings.resume_text_cache_dir),
            },
        )
        resume_texts_by_code = {
            resume_code: load_cached_resume_text(settings.resume_text_cache_dir, resume_code)
            for resume_code in resume_profile.resume_codes.values()
        }
        analytics.event(
            "resume_texts_loaded",
            status="success",
            profile=search_profile.name,
            metadata={
                "count": sum(1 for text in resume_texts_by_code.values() if text),
                "requested": len(resume_texts_by_code),
                "cache_dir": str(settings.resume_text_cache_dir),
            },
        )
        analytics.event(
            "search_opened",
            status="success",
            profile=search_profile.name,
            metadata={
                "query": search_profile.query,
                "advanced_search_url": bool(search_profile.advanced_search_url),
            },
        )
        open_search(context, search_profile)
        collection_limit = _collection_limit(effective_limit, target_new=target_new)
        vacancies = collect_vacancies(context, collection_limit)
        analytics.event(
            "vacancies_collected",
            status="success",
            profile=search_profile.name,
            metadata={
                "count": len(vacancies),
                "target_new": target_new,
                "effective_limit": effective_limit,
                "collection_limit": collection_limit,
            },
        )
        for vacancy_link in vacancies:
            if target_new and _new_attempt_count(stats) >= effective_limit:
                break
            stats.scanned += 1
            vacancy_text = ""
            resume_text = ""
            should_ignore_dry_run = ignore_dry_run_history and not effective_dry_run
            if store.has_processed(vacancy_link.vacancy_id, ignore_dry_run=should_ignore_dry_run):
                LOGGER.info("Skipping already processed vacancy: %s", vacancy_link.url)
                stats.record(Status.SKIPPED)
                analytics.vacancy_result(
                    search_profile.name,
                    vacancy_link,
                    Status.SKIPPED,
                    message="Already processed in local state.",
                )
                continue
            vacancy = open_vacancy(context, vacancy_link)
            selected_resume = ""
            selected_resume_code = ""
            try:
                analytics.event(
                    "vacancy_opened",
                    status="success",
                    profile=search_profile.name,
                    vacancy=vacancy,
                )
                if _should_apply_data_engineer_post_filter(search_profile.name) and not (
                    is_relevant_data_engineer_title(vacancy.title)
                ):
                    reason = "irrelevant_title"
                    message = "Irrelevant vacancy title skipped by Data Engineer post-filter."
                    store.record(vacancy, "", Status.SKIPPED, reason)
                    stats.record(Status.SKIPPED)
                    analytics.vacancy_result(
                        search_profile.name,
                        vacancy,
                        Status.SKIPPED,
                        message=message,
                        error_reason=reason,
                        metadata=review_metadata(
                            reason,
                            status=Status.SKIPPED.value,
                            message=message,
                        ),
                    )
                    LOGGER.info("Skipped irrelevant vacancy title %s: %s", vacancy.title, vacancy.url)
                    continue
                (
                    result,
                    selected_resume,
                    selected_resume_code,
                    vacancy_text,
                    resume_text,
                ) = respond_to_vacancy(
                    context,
                    settings,
                    resume_profile,
                    vacancy,
                    effective_dry_run,
                    resume_texts_by_code,
                    resume_fallback_text,
                    automation=response_automation,
                )
                store.record(
                    vacancy,
                    selected_resume,
                    result.status,
                    result.error_reason,
                    vacancy_text=vacancy_text,
                    resume_text=resume_text,
                )
                stats.record(result.status)
                analytics.vacancy_result(
                    search_profile.name,
                    vacancy,
                    result.status,
                    selected_resume=selected_resume,
                    message=result.message,
                    error_reason=result.error_reason,
                    metadata={
                        "vacancy_text": vacancy_text,
                        "resume_text": resume_text,
                        "resume_code": selected_resume_code,
                        **review_metadata(
                            result.error_reason,
                            status=result.status.value,
                            message=result.message,
                        ),
                    },
                )
                LOGGER.info("%s: %s", result.status.value, vacancy.url)
            except AutomationError as exc:
                diagnostics_path = capture_diagnostics(context, type(exc).__name__)
                store.record(
                    vacancy,
                    selected_resume,
                    Status.FAILURE,
                    str(exc),
                    vacancy_text=vacancy_text,
                    resume_text=resume_text,
                )
                stats.record(Status.FAILURE)
                analytics.vacancy_result(
                    search_profile.name,
                    vacancy,
                    Status.FAILURE,
                    selected_resume=selected_resume,
                    error_reason=str(exc),
                    diagnostics_path=diagnostics_path,
                    metadata={
                        "vacancy_text": vacancy_text,
                        "resume_text": resume_text,
                        "resume_code": selected_resume_code,
                        **review_metadata(str(exc), status=Status.FAILURE.value),
                    },
                )
                LOGGER.warning("Failed vacancy %s: %s", vacancy.url, exc)
                if target_new and _new_attempt_count(stats) >= effective_limit:
                    break
        analytics.run_finished(search_profile.name, stats)
    finally:
        close_context(context)
        store.close()
        analytics.close()
    return stats


def run_vacancy(vacancy_url: str, dry_run: bool | None) -> dict[str, object]:
    settings = Settings.load()
    settings.validate_for_browser(require_credentials=True)
    settings.ensure_dirs()
    resume_profile = load_resume_profile(settings)
    context = create_context(settings)
    llm_client = create_llm_client(settings)
    effective_dry_run = settings.dry_run if dry_run is None else dry_run
    store = StateStore(settings.state_db_path)
    analytics = DuckDBAnalytics(settings.analytics_db_path, new_run_id())
    resume_fallback_text = load_resume_text(settings.resume_text_path)
    response_automation = ResponseAutomation(
        llm=llm_client,
        analytics=analytics,
        profile_name="targeted-vacancy",
    )
    try:
        analytics.run_started("targeted-vacancy", 1, effective_dry_run)
        login_or_restore_session(context)
        sync_result = refresh_resume_text_cache(
            context,
            resume_profile.resume_codes,
            settings.resume_text_cache_dir,
        )
        resume_texts_by_code = {
            resume_code: load_cached_resume_text(settings.resume_text_cache_dir, resume_code)
            for resume_code in resume_profile.resume_codes.values()
        }
        vacancy = open_vacancy(
            context,
            Vacancy(url=vacancy_url, vacancy_id=_vacancy_id_from_url(vacancy_url)),
        )
        try:
            (
                result,
                selected_resume,
                selected_resume_code,
                vacancy_text,
                resume_text,
            ) = respond_to_vacancy(
                context,
                settings,
                resume_profile,
                vacancy,
                effective_dry_run,
                resume_texts_by_code,
                resume_fallback_text,
                automation=response_automation,
            )
            store.record(
                vacancy,
                selected_resume,
                result.status,
                result.error_reason,
                vacancy_text=vacancy_text,
                resume_text=resume_text,
            )
            analytics.vacancy_result(
                "targeted-vacancy",
                vacancy,
                result.status,
                selected_resume=selected_resume,
                message=result.message,
                error_reason=result.error_reason,
                metadata={
                    "vacancy_text": vacancy_text,
                    "resume_text": resume_text,
                    "resume_code": selected_resume_code,
                    "sync_result": sync_result,
                    **review_metadata(
                        result.error_reason,
                        status=result.status.value,
                        message=result.message,
                    ),
                },
            )
            analytics.run_finished("targeted-vacancy", RunStats(scanned=1, sent=int(result.status == Status.SUCCESS), dry_run=int(result.status == Status.DRY_RUN), skipped=int(result.status == Status.SKIPPED), failed=int(result.status == Status.FAILURE)))
            return {
                "status": result.status,
                "message": result.message,
                "error_reason": result.error_reason,
                "selected_resume": selected_resume,
                "selected_resume_code": selected_resume_code,
                "vacancy_url": vacancy.url,
                "vacancy_title": vacancy.title,
                "vacancy_company": vacancy.company,
                "vacancy_text_len": len(vacancy_text or ""),
                "resume_text_len": len(resume_text or ""),
            }
        except AutomationError as exc:
            diagnostics_path = capture_diagnostics(context, type(exc).__name__)
            store.record(
                vacancy,
                "",
                Status.FAILURE,
                str(exc),
            )
            analytics.vacancy_result(
                "targeted-vacancy",
                vacancy,
                Status.FAILURE,
                error_reason=str(exc),
                diagnostics_path=diagnostics_path,
                metadata=review_metadata(str(exc), status=Status.FAILURE.value),
            )
            analytics.run_finished("targeted-vacancy", RunStats(scanned=1, failed=1))
            raise
    finally:
        close_context(context)
        store.close()
        analytics.close()


def login_only(manual: bool = False) -> None:
    settings = Settings.load()
    settings.validate_for_browser(require_credentials=True)
    settings.ensure_dirs()
    analytics = DuckDBAnalytics(settings.analytics_db_path, new_run_id())
    context = create_context(settings)
    try:
        analytics.event(
            "login_started",
            status="manual" if manual else "started",
            profile=settings.search_profile,
        )
        if manual:
            wait_for_manual_login(context)
        else:
            login_or_restore_session(context)
        analytics.event(
            "login_finished",
            status="success",
            profile=settings.search_profile,
        )
    finally:
        close_context(context)
        analytics.close()


def _vacancy_id_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] if "/vacancy/" in path else ""


def _should_apply_data_engineer_post_filter(profile_name: str) -> bool:
    return profile_name.casefold().startswith("data-engineer")


def _collection_limit(effective_limit: int, *, target_new: bool) -> int:
    if not target_new:
        return effective_limit
    return max(effective_limit * 8, effective_limit + 25)


def _new_attempt_count(stats: RunStats) -> int:
    return stats.sent + stats.failed + stats.dry_run
