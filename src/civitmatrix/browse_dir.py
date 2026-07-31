"""Native folder picker for the local UI (Browse…)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def browse_directory(start: str | None = None) -> dict[str, Any]:
    """Open a desktop folder dialog. Returns {path} or {cancelled: true} or {error}."""
    initial = Path(start or os.environ.get("HOME") or ".").expanduser()
    if not initial.is_dir():
        initial = initial.parent if initial.parent.is_dir() else Path.home()
    start_s = str(initial)

    def _normalize_path(chosen: str) -> str:
        """Prefer native separators (backslash on Windows) for UI display."""
        try:
            return str(Path(chosen))
        except (OSError, ValueError):
            return chosen.replace("/", "\\") if os.name == "nt" else chosen

    if shutil.which("zenity"):
        try:
            proc = subprocess.run(
                [
                    "zenity",
                    "--file-selection",
                    "--directory",
                    f"--filename={start_s}/",
                    "--title=Select output folder",
                ],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"error": str(e)}
        if proc.returncode == 0 and proc.stdout.strip():
            return {"path": _normalize_path(proc.stdout.strip())}
        return {"cancelled": True}

    if shutil.which("kdialog"):
        try:
            proc = subprocess.run(
                ["kdialog", "--getexistingdirectory", start_s],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"error": str(e)}
        if proc.returncode == 0 and proc.stdout.strip():
            return {"path": _normalize_path(proc.stdout.strip())}
        return {"cancelled": True}

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:  # noqa: BLE001
        return {
            "error": (
                "No folder dialog available (install zenity/kdialog, or tkinter). "
                f"Detail: {e}"
            )
        }

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(
            initialdir=start_s,
            title="Select output folder",
            mustexist=True,
        )
    finally:
        root.destroy()
    if chosen:
        return {"path": _normalize_path(chosen)}
    return {"cancelled": True}
