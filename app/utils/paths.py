from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ElevenManager"
APP_VERSION = "1.0.0"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _user_data_root() -> Path:
    if sys.platform.startswith("win"):
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / APP_NAME


def _compute_base_dir() -> Path:
    if is_frozen():
        candidate = Path(sys.executable).resolve().parent
    else:
        candidate = Path(__file__).resolve().parents[2]
    # portable mode if we can write next to the exe, otherwise fall back to appdata
    if _is_writable(candidate / "data"):
        return candidate
    return _user_data_root()


BASE_DIR: Path = _compute_base_dir()


def resource_path(relative: str) -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", BASE_DIR)) / relative
    return Path(__file__).resolve().parents[2] / relative


def data_dir() -> Path:
    return BASE_DIR / "data"


def default_output_dir() -> Path:
    return BASE_DIR / "output"


def logs_dir() -> Path:
    return data_dir() / "logs"


def backups_dir() -> Path:
    return data_dir() / "backups"


def cache_dir() -> Path:
    return data_dir() / "cache"


def data_file(name: str) -> Path:
    return data_dir() / name


def ensure_dirs() -> None:
    for d in (data_dir(), default_output_dir(), logs_dir(), backups_dir(), cache_dir()):
        d.mkdir(parents=True, exist_ok=True)
