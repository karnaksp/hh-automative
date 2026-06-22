"""Logging setup."""

from __future__ import annotations

import logging
from pathlib import Path

from hh_automative.analytics import DuckDBLogHandler


def configure_logging(log_dir: Path, analytics_db_path: Path | None = None) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(log_dir / "app.log", encoding="utf-8"),
    ]
    if analytics_db_path is not None:
        handlers.append(DuckDBLogHandler(analytics_db_path))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
