"""Admin dashboard launcher."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def _is_port_free(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) != 0
    except OSError:
        return False


def _pick_free_port(host: str, start: int) -> int:
    for candidate in range(start, start + 50):
        if _is_port_free(host, candidate):
            return candidate
    raise RuntimeError(f"No free port found in range {start}-{start + 49}")


def _occupying_processes(port: int) -> list[str]:
    """Return command lines of processes occupying a TCP port."""
    pids = _occupying_pids(port)
    if not pids:
        return []

    process_lines: list[str] = []
    for pid in pids:
        if not pid:
            continue
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            continue

        if not result.stdout:
            continue
        command = result.stdout.strip()
        if command:
            process_lines.append(f"{pid}: {command}")

    if process_lines:
        return process_lines

    # Fallback to raw lsof output when ps is unavailable.
    try:
        result = subprocess.run(
            ["lsof", "-n", "-P", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []

    return [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()]


def _occupying_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-n", "-P", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []

    pids: list[int] = []
    for token in result.stdout.splitlines():
        token = token.strip()
        if token.isdigit():
            pids.append(int(token))
    return pids


def _is_our_dashboard_process(process_line: str) -> bool:
    normalized = process_line.lower()
    return (
        ("streamlit" in normalized and "run" in normalized)
        and ("dashboard/app.py" in normalized or "/dashboard/app.py" in normalized)
    )


def _stale_project_dashboard_pids() -> list[int]:
    """Find already running Streamlit dashboard processes for this checkout."""
    app_path = str(_streamlit_app_path())
    relative_app_path = os.path.join("dashboard", "app.py")
    current_pid = os.getpid()
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=", "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []

    pids: list[int] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == current_pid:
            continue
        normalized = command.lower()
        if "streamlit" not in normalized or "run" not in normalized:
            continue
        if app_path in command or relative_app_path in command:
            pids.append(pid)
    return sorted(set(pids))


def _kill_processes(pids: list[int]) -> None:
    if not pids:
        return

    subprocess.run(["kill", "-TERM"] + [str(pid) for pid in pids], check=False)

    deadline = time.monotonic() + 2.5
    remaining = set(pids)
    while time.monotonic() < deadline and remaining:
        for pid in list(remaining):
            probe = subprocess.run(["kill", "-0", str(pid)], capture_output=True, check=False)
            if probe.returncode != 0:
                remaining.remove(pid)
        if remaining:
            time.sleep(0.1)

    if remaining:
        subprocess.run(["kill", "-KILL"] + [str(pid) for pid in sorted(remaining)], check=False)


def _streamlit_app_path() -> Path:
    return Path(__file__).resolve().parent.parent / "dashboard" / "app.py"


def _streamlit_invocation() -> tuple[list[str], str]:
    candidates: list[list[str]] = []

    project_root = Path(__file__).resolve().parent.parent
    venv_python = project_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        candidates.append([str(venv_python), "-m", "streamlit"])

    candidates.append([sys.executable, "-m", "streamlit"])
    streamlit_in_path = shutil.which("streamlit")
    if streamlit_in_path is not None:
        candidates.append(["streamlit"])

    for candidate in candidates:
        try:
            check = subprocess.run(
                [*candidate, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            continue
        if check.returncode == 0:
            version = check.stdout.strip() or check.stderr.strip()
            return candidate, version
    return [], ""


def launch_dashboard(
    host: str = "127.0.0.1",
    port: int = 8501,
    db_path: Path | None = None,
    auto_port: bool = True,
    kill_existing: bool = False,
) -> int:
    """Start Streamlit dashboard bound to a stable local entrypoint.

    Returns Streamlit exit code.
    """
    db_path = db_path or Path("data/hh_automative.duckdb")
    dashboard_file = _streamlit_app_path()
    if not dashboard_file.exists():
        print(f"Не найден файл дашборда: {dashboard_file}")
        return 3

    if kill_existing:
        stale_pids = _stale_project_dashboard_pids()
        if stale_pids:
            print(f"Останавливаю старые процессы админки hh-automative: {stale_pids}")
            _kill_processes(stale_pids)

    if not _is_port_free(host, port):
        occupants = _occupying_processes(port)
        if occupants:
            print(f"Порт {port} сейчас занят (TCP LISTEN):")
            for line in occupants:
                print(f"  {line}")
            if not kill_existing and any(_is_our_dashboard_process(line) for line in occupants):
                print("Видимо, админка hh-automative уже запущена на этом порту.")
                print(f"Открой: http://{host}:{port}")
                print("Чтобы перезапустить, останови процесс вручную или используй --kill-existing.")
                return 0

        if kill_existing:
            pids = _occupying_pids(port)
            if pids:
                print(f"Останавливаю процессы на порту {port}: {pids}")
                _kill_processes(pids)
                if not _is_port_free(host, port):
                    print(f"Не удалось освободить порт {port} после завершения процессов.")
                    if not auto_port:
                        return 4
                    try:
                        port = _pick_free_port(host, port + 1)
                    except RuntimeError as exc:
                        print(f"Не удалось выбрать свободный порт: {exc}")
                        return 4
                    print(f"Запускаю админку на свободном порту: {port}")
                    print(f"URL: http://{host}:{port}")
            if _is_port_free(host, port):
                print(f"Порт {port} освобождён, продолжаю запуск на том же порту.")
                print(f"URL: http://{host}:{port}")
            elif auto_port:
                try:
                    port = _pick_free_port(host, port + 1)
                except RuntimeError as exc:
                    print(f"Не удалось выбрать свободный порт: {exc}")
                    return 4
                print(f"Запускаю админку на свободном порту: {port}")
                print(f"URL: http://{host}:{port}")
            else:
                print(f"Порт {port} уже занят в системе.")
                print("Укажи другой порт: --port 8502")
                print(f"Или принудительно открой тот же порт: --no-auto-port --port {port}")
                print("Если это тестовый/другой Streamlit, завершить его можно так:")
                print(f"  lsof -iTCP:{port} -sTCP:LISTEN -t | xargs kill")
                return 4
        elif auto_port:
            try:
                port = _pick_free_port(host, port + 1)
            except RuntimeError as exc:
                print(f"Не удалось выбрать свободный порт: {exc}")
                return 4
            print(f"Запускаю админку на свободном порту: {port}")
            print(f"URL: http://{host}:{port}")
        else:
            print(f"Порт {port} уже занят в системе.")
            print("Укажи другой порт: --port 8502")
            print(f"Или принудительно открой тот же порт: --no-auto-port --port {port}")
            print("Если это тестовый/другой Streamlit, завершить его можно так:")
            print(f"  lsof -iTCP:{port} -sTCP:LISTEN -t | xargs kill")
            return 4
    streamlit_invocation, streamlit_version = _streamlit_invocation()
    if not streamlit_invocation:
        print("Streamlit не найден в текущем окружении.")
        print("Рекомендуем запустить команду из .venv проекта.")
        print("Примеры:")
        print("  source .venv/bin/activate")
        print("  .venv/bin/python -m hh-automative dashboard --db-path data/hh_automative.duckdb")
        print("Или установи зависимости: pip install -e .[dev]")
        return 1

    command = [
        *streamlit_invocation,
        "run",
        str(dashboard_file),
        "--server.address",
        host,
        "--server.port",
        str(port),
    ]

    env = os.environ.copy()
    env["HH_DASHBOARD_DB_PATH"] = str(db_path)
    if streamlit_version:
        print(streamlit_version, flush=True)

    print(f"Запуск админки: http://{host}:{port}", flush=True)
    print(f"Файл БД: {db_path}", flush=True)
    print(f"Команда: {' '.join(command)}", flush=True)
    print("Нажми Ctrl+C для остановки админки.", flush=True)

    process = subprocess.Popen(command, env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()
