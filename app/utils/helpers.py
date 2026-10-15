from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.i18n import tr
from app.utils.paths import BASE_DIR


def mask_key(key: str) -> str:
    key = key.strip()
    if not key:
        return ""
    if key.startswith("sk_"):
        return key[:7] + "****"
    return key[:4] + "****"


def fmt_int(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", " ")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return tr("{n} s", n=seconds)
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return tr("{m} min {s:02d} s", m=minutes, s=sec) if sec else tr("{m} min", m=minutes)
    hours, minutes = divmod(minutes, 60)
    return tr("{h} h {m:02d} min", h=hours, m=minutes)


def fmt_reset(unix: int | None) -> str:
    if not unix:
        return "—"
    return datetime.fromtimestamp(unix).strftime("%d.%m.%Y %H:%M")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or "untitled")[:max_len]


def render_template(template: str, *, voice: str, model: str, index: int, title: str = "") -> str:
    now = datetime.now()
    values = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H-%M-%S"),
        "voice": voice or "voice",
        "model": model or "model",
        "index": f"{index:03d}",
        "title": title or "text",
    }
    try:
        result = template.format(**values)
    except (KeyError, IndexError, ValueError):
        logger.warning("Bad file name template: {}", template)
        result = "{date}_{voice}_{index}".format(**values)
    return sanitize_filename(result)


def text_title(text: str, words: int = 6) -> str:
    parts = re.findall(r"\w+", text)[:words]
    return " ".join(parts) or tr("Untitled")


def open_path(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        logger.error("Failed to open {}: {}", path, e)


def reveal_in_folder(path: Path) -> None:
    try:
        if path.is_file() and sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif path.is_file() and sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            open_path(path if path.is_dir() else path.parent)
    except OSError as e:
        logger.error("Failed to reveal {}: {}", path, e)


def find_ffmpeg() -> Path | None:
    exe = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    for candidate in (BASE_DIR / "ffmpeg" / "bin" / exe, BASE_DIR / "ffmpeg" / exe, BASE_DIR / exe):
        if candidate.is_file():
            return candidate
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def ffmpeg_instructions() -> str:
    if sys.platform.startswith("win"):
        return (
            tr("ffmpeg not found.\n\n"
               "Option 1 (easiest): open PowerShell and run\n"
               "    winget install Gyan.FFmpeg\n"
               "then restart the app.\n\n"
               "Option 2: download the archive from https://www.gyan.dev/ffmpeg/builds/ (release essentials), "
               "unpack it and put the bin folder next to the app so you get:\n    {path}",
               path=BASE_DIR / "ffmpeg" / "bin" / "ffmpeg.exe")
        )
    if sys.platform == "darwin":
        return (
            tr("ffmpeg not found.\n\n"
               "Install it with Homebrew:\n    brew install ffmpeg\n"
               "No Homebrew? https://brew.sh\nThen restart the app.")
        )
    return (
        tr("ffmpeg not found.\n\n"
           "Debian/Ubuntu:  sudo apt install ffmpeg\n"
           "Fedora:         sudo dnf install ffmpeg\n"
           "Arch:           sudo pacman -S ffmpeg\n\n"
           "Then restart the app.")
    )
