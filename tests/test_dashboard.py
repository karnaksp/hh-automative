from __future__ import annotations

from hh_automative import dashboard


def test_dashboard_kills_occupied_processes_when_kill_existing(monkeypatch, tmp_path):
    kill_calls: list[int] = []
    pids = [1001]
    checks = {"count": 0}

    monkeypatch.setattr(dashboard, "_stale_project_dashboard_pids", lambda: [])
    monkeypatch.setattr(dashboard, "_is_port_free", lambda host, port: checks.update(count=checks["count"] + 1) or checks["count"] > 1)
    monkeypatch.setattr(
        dashboard,
        "_occupying_processes",
        lambda port: ["1001: /home/user/.venv/bin/python -m streamlit run dashboard/app.py"],
    )
    monkeypatch.setattr(dashboard, "_occupying_pids", lambda port: pids)
    monkeypatch.setattr(dashboard, "_kill_processes", lambda pids_to_kill: kill_calls.extend(pids_to_kill))

    captured_command: dict[str, object] = {}

    def fake_invocation() -> tuple[list[str], str]:
        return ["python", "-m", "streamlit"], "streamlit 9.9.0"

    class _FakePopen:
        def __init__(self, command: list[str], env: dict[str, str]) -> None:
            captured_command["command"] = command
            captured_command["env"] = env

        def wait(self) -> int:
            return 0

        def terminate(self) -> None:
            pass

    monkeypatch.setattr(dashboard, "_streamlit_invocation", fake_invocation)
    monkeypatch.setattr(dashboard.subprocess, "Popen", _FakePopen)

    exit_code = dashboard.launch_dashboard(
        host="127.0.0.1",
        port=8501,
        db_path=tmp_path / "hh.db",
        auto_port=False,
        kill_existing=True,
    )

    assert exit_code == 0
    assert kill_calls == pids
    assert captured_command["command"] == [
        "python",
        "-m",
        "streamlit",
        "run",
        str(dashboard._streamlit_app_path()),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8501",
    ]
    assert captured_command["env"]["HH_DASHBOARD_DB_PATH"] == str(tmp_path / "hh.db")


def test_dashboard_no_kill_existing_returns_when_our_process_found(monkeypatch, tmp_path):
    occupied = ["1002: /tmp/env/bin/python -m streamlit run dashboard/app.py"]
    monkeypatch.setattr(dashboard, "_stale_project_dashboard_pids", lambda: [])
    monkeypatch.setattr(dashboard, "_is_port_free", lambda host, port: False)
    monkeypatch.setattr(dashboard, "_occupying_processes", lambda port: occupied)
    monkeypatch.setattr(dashboard, "_streamlit_invocation", lambda: (["python", "-m", "streamlit"], "streamlit 9.9.0"))

    exit_code = dashboard.launch_dashboard(
        host="127.0.0.1",
        port=8501,
        db_path=tmp_path / "hh.db",
        auto_port=False,
        kill_existing=False,
    )

    assert exit_code == 0


def test_dashboard_kills_stale_project_processes_before_start(monkeypatch, tmp_path):
    kill_calls: list[int] = []
    captured_command: dict[str, object] = {}

    monkeypatch.setattr(dashboard, "_stale_project_dashboard_pids", lambda: [2001, 2002])
    monkeypatch.setattr(dashboard, "_kill_processes", lambda pids_to_kill: kill_calls.extend(pids_to_kill))
    monkeypatch.setattr(dashboard, "_is_port_free", lambda host, port: True)
    monkeypatch.setattr(dashboard, "_streamlit_invocation", lambda: (["python", "-m", "streamlit"], "streamlit 9.9.0"))

    class _FakePopen:
        def __init__(self, command: list[str], env: dict[str, str]) -> None:
            captured_command["command"] = command
            captured_command["env"] = env

        def wait(self) -> int:
            return 0

        def terminate(self) -> None:
            pass

    monkeypatch.setattr(dashboard.subprocess, "Popen", _FakePopen)

    exit_code = dashboard.launch_dashboard(
        host="127.0.0.1",
        port=8501,
        db_path=tmp_path / "hh.db",
        auto_port=False,
        kill_existing=True,
    )

    assert exit_code == 0
    assert kill_calls == [2001, 2002]
    assert captured_command["command"][0:3] == ["python", "-m", "streamlit"]
