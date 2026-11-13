"""Executable entry point for the panel and its detached background worker."""

import ctypes
import sys

from src.app_paths import FROZEN
from src.export_jobs import main as run_worker
from src.export_panel import main as open_panel


if __name__ == "__main__":
    if FROZEN:
        # Power BI and Windows Task Scheduler use the system DLL search path.
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    if sys.argv[1:2] == ["--worker"]:
        del sys.argv[1]
        raise SystemExit(run_worker())
    open_panel()
