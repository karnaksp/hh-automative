"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hh_automative.dashboard import launch_dashboard
from hh_automative.errors import ConfigError, HhAutomativeError
from hh_automative.logging_utils import configure_logging

if TYPE_CHECKING:
    from hh_automative.settings import Settings

LOGGER = logging.getLogger(__name__)


def _print_json(data: dict[str, object]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "dashboard":
        return _run_dashboard(args)

    from hh_automative.settings import Settings

    settings = Settings.load()
    settings.ensure_dirs()
    analytics_db_path = None if args.command == "analytics" else settings.analytics_db_path
    configure_logging(settings.log_dir, analytics_db_path)

    try:
        if args.command == "check-config":
            _check_config(settings, args.profile)
            return 0
        if args.command == "login":
            from hh_automative.runner import login_only

            login_only(manual=args.manual)
            return 0
        if args.command == "run":
            from hh_automative.runner import run

            stats = run(
                args.profile,
                args.limit,
                args.dry_run,
                target_new=args.target_new,
                ignore_dry_run_history=args.ignore_dry_run_history,
            )
            print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "run-vacancy":
            from hh_automative.runner import run_vacancy

            result = run_vacancy(str(args.vacancy_url), args.dry_run)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.command == "stats":
            _print_stats(settings)
            return 0
        if args.command == "analytics":
            _print_analytics(settings)
            return 0
        if args.command == "sync-resumes":
            from hh_automative.resume_sync import sync_resume_config

            _print_json(sync_resume_config())
            return 0
        if args.command == "llm-check":
            from hh_automative.llm import llm_config_status

            _print_json(llm_config_status(settings))
            return 0
        if args.command == "llm-cover-letter":
            from hh_automative.llm_cli import llm_cover_letter

            _print_json(llm_cover_letter(vacancy_text_path=args.vacancy_text_file, ask=args.ask))
            return 0
        if args.command == "llm-questionnaire":
            from hh_automative.llm_cli import llm_questionnaire

            _print_json(
                llm_questionnaire(
                    vacancy_text_path=args.vacancy_text_file,
                    questions_json_path=args.questions_json_file,
                    ask=args.ask,
                )
            )
            return 0
    except HhAutomativeError as exc:
        LOGGER.error("%s", exc)
        return 2
    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hh-automative")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_config = subparsers.add_parser("check-config")
    check_config.add_argument("--profile", default=None)

    login_parser = subparsers.add_parser("login")
    login_parser.add_argument(
        "--manual",
        action="store_true",
        help="Open browser and wait for manual login, then save auth session.",
    )

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--profile", default=None)
    run_parser.add_argument("--limit", type=int, default=None)
    run_parser.add_argument(
        "--target-new",
        action="store_true",
        help=(
            "Treat --limit as the number of non-skipped new attempts. "
            "Already processed/responded and irrelevant vacancies do not consume the quota."
        ),
    )
    dry_run_history_group = run_parser.add_mutually_exclusive_group()
    dry_run_history_group.add_argument(
        "--ignore-dry-run-history",
        dest="ignore_dry_run_history",
        action="store_true",
        help="For real runs, do not treat previous dry-run rows as already processed.",
    )
    dry_run_history_group.add_argument(
        "--respect-dry-run-history",
        dest="ignore_dry_run_history",
        action="store_false",
        help="Treat previous dry-run rows as already processed.",
    )
    dry_run_group = run_parser.add_mutually_exclusive_group()
    dry_run_group.add_argument("--dry-run", action="store_true", dest="dry_run")
    dry_run_group.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    run_parser.set_defaults(dry_run=None, ignore_dry_run_history=True)

    run_vacancy_parser = subparsers.add_parser("run-vacancy")
    run_vacancy_parser.add_argument("vacancy_url")
    run_vacancy_dry_run_group = run_vacancy_parser.add_mutually_exclusive_group()
    run_vacancy_dry_run_group.add_argument("--dry-run", action="store_true", dest="dry_run")
    run_vacancy_dry_run_group.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    run_vacancy_parser.set_defaults(dry_run=None)

    subparsers.add_parser("stats")
    subparsers.add_parser("analytics")
    subparsers.add_parser("sync-resumes")
    subparsers.add_parser("llm-check")

    llm_cover_letter_parser = subparsers.add_parser("llm-cover-letter")
    llm_cover_letter_parser.add_argument("vacancy_text_file", type=Path)
    llm_cover_letter_parser.add_argument(
        "--ask",
        action="store_true",
        help="Call the configured API provider. Without this flag only prints/logs the prompt.",
    )

    llm_questionnaire_parser = subparsers.add_parser("llm-questionnaire")
    llm_questionnaire_parser.add_argument("vacancy_text_file", type=Path)
    llm_questionnaire_parser.add_argument("questions_json_file", type=Path)
    llm_questionnaire_parser.add_argument(
        "--ask",
        action="store_true",
        help="Call the configured API provider. Without this flag only prints/logs the prompt.",
    )

    dashboard_parser = subparsers.add_parser("dashboard")
    dashboard_parser.add_argument(
        "--db-path",
        default=os.getenv(
            "HH_DASHBOARD_DB_PATH",
            os.getenv("HH_ANALYTICS_DB_PATH", "data/hh_automative.duckdb"),
        ),
        help="Path to DuckDB file for dashboard tables.",
    )
    dashboard_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for Streamlit.",
    )
    dashboard_parser.add_argument(
        "--port",
        type=int,
        default=8501,
        help="Port for Streamlit dashboard.",
    )
    dashboard_parser.add_argument(
        "--auto-port",
        action="store_true",
        help="Если порт занят, выбрать ближайший свободный порт автоматически.",
    )
    dashboard_parser.add_argument(
        "--no-auto-port",
        action="store_false",
        dest="auto_port",
        help="Не переключать порт автоматически, если 8501 занят.",
    )
    dashboard_parser.add_argument(
        "--kill-existing",
        action="store_true",
        help="Остановить процессы на занятом порту перед запуском админки.",
    )
    dashboard_parser.add_argument(
        "--no-kill-existing",
        action="store_false",
        dest="kill_existing",
        help="Не останавливать процессы на занятом порту.",
    )
    dashboard_parser.set_defaults(auto_port=True, kill_existing=True)

    return parser


def _run_dashboard(args: Any) -> int:
    try:
        launch_kwargs: dict[str, object] = {
            "host": args.host,
            "port": args.port,
            "db_path": Path(args.db_path) if args.db_path else None,
            "auto_port": bool(args.auto_port),
            "kill_existing": bool(args.kill_existing),
        }
        return int(launch_dashboard(**launch_kwargs))
    except HhAutomativeError as exc:
        LOGGER.error("%s", exc)
        return 2


def _check_config(settings: Settings, profile_name: str | None) -> None:
    from hh_automative.settings import load_resume_profile, load_search_profile

    settings.validate_for_browser(require_credentials=False)
    profile = load_search_profile(settings, profile_name)
    resumes = load_resume_profile(settings)
    if not profile.query and not profile.advanced_search_url:
        raise ConfigError("Search profile must define query or advanced_search_url.")
    print(
        json.dumps(
            {
                "profile": profile.name,
                "query": profile.query,
                "advanced_search_url": bool(profile.advanced_search_url),
                "default_resume": resumes.default_resume,
                "state_db_path": str(settings.state_db_path),
                "analytics_db_path": str(settings.analytics_db_path),
                "use_llm": settings.use_llm,
                "use_llm_cover_letter": settings.use_llm_cover_letter,
                "use_llm_questionnaire": settings.use_llm_questionnaire,
                "llm_provider": settings.llm_provider,
                "llm_fallbacks": settings.llm_fallbacks,
                "resume_text_path": str(settings.resume_text_path),
                "resume_text_cache_dir": str(settings.resume_text_cache_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _print_stats(settings: Settings) -> None:
    from hh_automative.state import StateStore

    store = StateStore(settings.state_db_path)
    try:
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    finally:
        store.close()


def _print_analytics(settings: Settings) -> None:
    from hh_automative.analytics import analytics_summary

    print(json.dumps(analytics_summary(settings.analytics_db_path), ensure_ascii=False, indent=2))
