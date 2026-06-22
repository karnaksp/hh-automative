"""Run local quality gates."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    commands = [
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "pytest"],
        [
            sys.executable,
            "-m",
            "compileall",
            "hh_automative",
            "dashboard",
            "config",
            "utils",
            "scripts",
            "tests",
            "main.py",
        ],
    ]
    for command in commands:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
