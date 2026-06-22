"""Launch ad-hoc automation runs from the dashboard."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hh_automative.errors import ConfigError
from hh_automative.settings import Settings


@dataclass(frozen=True, slots=True)
class PipelineLaunchRequest:
    query: str
    resume_name: str
    limit: int
    dry_run: bool
    target_new: bool = True
    ignore_dry_run_history: bool = True
    headless: bool = True
    exclude: str = ""
    min_salary: str = ""
    only_with_salary: bool = False
    advanced_search_url: str = ""
    label: str = ""


@dataclass(frozen=True, slots=True)
class PipelineLaunchResult:
    pid: int
    profile_name: str
    search_profiles_path: Path
    resumes_path: Path
    log_path: Path
    command: list[str]


def available_resume_names(settings: Settings | None = None) -> list[str]:
    settings = settings or Settings.load()
    data = _read_json(settings.resumes_path)
    return sorted(str(name) for name in data.get("resume_codes", {}) if str(name).strip())


def launch_pipeline(
    request: PipelineLaunchRequest,
    *,
    settings: Settings | None = None,
    cwd: Path | None = None,
) -> PipelineLaunchResult:
    settings = settings or Settings.load()
    _validate_request(request, settings)
    root = cwd or Path.cwd()
    runtime_dir = root / "data" / "runtime-runs"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    launch_id = _launch_id(request.label)
    profile_name = f"adhoc-{launch_id}"
    search_profiles_path = runtime_dir / f"{profile_name}.search_profiles.json"
    resumes_path = runtime_dir / f"{profile_name}.resumes.json"
    log_path = runtime_dir / f"{profile_name}.log"

    resume_codes = _read_json(settings.resumes_path).get("resume_codes", {})
    selected_code = str(resume_codes[request.resume_name])
    _write_json(
        search_profiles_path,
        {
            profile_name: {
                "query": request.query,
                "exclude": request.exclude,
                "region": "global",
                "min_salary": request.min_salary,
                "only_with_salary": request.only_with_salary,
                "advanced_search_url": request.advanced_search_url,
            }
        },
    )
    _write_json(
        resumes_path,
        {
            "default_resume": request.resume_name,
            "resume_codes": {request.resume_name: selected_code},
        },
    )

    command = [
        sys.executable,
        "-m",
        "hh_automative",
        "run",
        "--profile",
        profile_name,
        "--limit",
        str(request.limit),
        "--dry-run" if request.dry_run else "--no-dry-run",
    ]
    if request.target_new:
        command.append("--target-new")
    if request.ignore_dry_run_history:
        command.append("--ignore-dry-run-history")

    env = os.environ.copy()
    env.update(
        {
            "HH_SEARCH_PROFILES_PATH": str(search_profiles_path),
            "HH_RESUMES_PATH": str(resumes_path),
            "HH_HEADLESS": "true" if request.headless else "false",
            "HH_SEARCH_PROFILE": profile_name,
        }
    )
    log_handle = log_path.open("a", encoding="utf-8")
    log_handle.write(f"\n=== launch {datetime.now(UTC).isoformat()} ===\n")
    log_handle.write(" ".join(command) + "\n")
    log_handle.flush()
    process = subprocess.Popen(
        command,
        cwd=str(root),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return PipelineLaunchResult(
        pid=process.pid,
        profile_name=profile_name,
        search_profiles_path=search_profiles_path,
        resumes_path=resumes_path,
        log_path=log_path,
        command=command,
    )


def _validate_request(request: PipelineLaunchRequest, settings: Settings) -> None:
    if not request.advanced_search_url and not request.query.strip():
        raise ConfigError("Query or advanced search URL is required.")
    if request.limit < 1:
        raise ConfigError("Limit must be greater than zero.")
    resume_codes = _read_json(settings.resumes_path).get("resume_codes", {})
    if request.resume_name not in resume_codes:
        raise ConfigError(f"Resume '{request.resume_name}' is not configured.")


def _launch_id(label: str) -> str:
    prefix = "".join(ch for ch in label.lower().strip().replace(" ", "-") if ch.isalnum() or ch == "-")
    if not prefix:
        prefix = "run"
    prefix = prefix[:32].strip("-") or "run"
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{timestamp}-{uuid4().hex[:8]}"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
