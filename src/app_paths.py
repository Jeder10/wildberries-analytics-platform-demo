"""Locate shipped resources and persistent files for both Windows entry points."""

import os
from pathlib import Path
import sys


FROZEN = getattr(sys, "frozen", False)
RESOURCE_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(sys.executable).resolve().parent if FROZEN else RESOURCE_ROOT


def worker_command(*args: str, windowless: bool = False) -> list[str]:
    if FROZEN:
        return [sys.executable, "--worker", *args]
    interpreter = "pythonw.exe" if windowless else "python.exe"
    return [str(APP_ROOT / ".venv" / "Scripts" / interpreter), "-X", "utf8", "-B", "-m", "src.export_jobs", *args]


def worker_environment() -> dict[str, str] | None:
    if FROZEN:
        # A job can continue even after the panel's one-file bundle is cleaned up.
        return {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    return None
