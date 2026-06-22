from __future__ import annotations

from hh_automative.cli import _build_parser


def test_dashboard_defaults_to_auto_port() -> None:
    parser = _build_parser()
    args = parser.parse_args(["dashboard"])
    assert args.auto_port is True


def test_dashboard_no_auto_port_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(["dashboard", "--no-auto-port"])
    assert args.auto_port is False


def test_dashboard_kill_existing_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(["dashboard", "--kill-existing"])
    assert args.kill_existing is True


def test_run_vacancy_parser_accepts_url_and_dry_run_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["run-vacancy", "https://spb.hh.ru/vacancy/134373649?from=applicant_recommended", "--no-dry-run"]
    )

    assert args.command == "run-vacancy"
    assert args.vacancy_url.startswith("https://spb.hh.ru/vacancy/134373649")
    assert args.dry_run is False


def test_run_parser_accepts_target_new_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "run",
            "--profile",
            "data-engineer",
            "--limit",
            "10",
            "--target-new",
            "--ignore-dry-run-history",
        ]
    )

    assert args.command == "run"
    assert args.profile == "data-engineer"
    assert args.limit == 10
    assert args.target_new is True
    assert args.ignore_dry_run_history is True


def test_run_parser_ignores_dry_run_history_by_default() -> None:
    parser = _build_parser()
    args = parser.parse_args(["run", "--profile", "data-engineer", "--limit", "10"])

    assert args.ignore_dry_run_history is True


def test_run_parser_can_respect_dry_run_history() -> None:
    parser = _build_parser()
    args = parser.parse_args(["run", "--profile", "data-engineer", "--respect-dry-run-history"])

    assert args.ignore_dry_run_history is False
