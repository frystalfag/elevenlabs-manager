from __future__ import annotations

import re
import sys
from typing import Any, Callable

from loguru import logger

from app.utils.paths import logs_dir

# new keys start with sk_, legacy ones are 32 hex chars
_KEY_PATTERNS = [
    re.compile(r"\bsk_[A-Za-z0-9]{4}([A-Za-z0-9]{8,})"),
    re.compile(r"\b([0-9a-f]{4})[0-9a-f]{28}\b"),
]


def mask_secrets(text: str) -> str:
    text = _KEY_PATTERNS[0].sub(lambda m: m.group(0)[:7] + "****", text)
    text = _KEY_PATTERNS[1].sub(lambda m: m.group(1) + "****", text)
    return text


def _patch(record: dict[str, Any]) -> None:
    record["message"] = mask_secrets(record["message"])


_configured = False


def setup_logger(debug: bool = False) -> None:
    global _configured
    if _configured:
        return
    logger.remove()
    logger.configure(patcher=_patch)
    fmt = "{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{line} | {message}"
    # stderr is None in a windowed exe
    if sys.stderr is not None:
        logger.add(sys.stderr, level="DEBUG" if debug else "INFO", format=fmt)
    logger.add(
        logs_dir() / "app.log",
        level="DEBUG",
        format=fmt,
        rotation="5 MB",
        retention=10,
        encoding="utf-8",
        enqueue=True,
    )
    _configured = True


def add_ui_sink(callback: Callable[[str, str], None]) -> int:
    def sink(message: Any) -> None:
        rec = message.record
        line = f"{rec['time']:%H:%M:%S} {rec['level'].name:<7} {rec['message']}"
        try:
            callback(rec["level"].name, line)
        except RuntimeError:
            pass

    return logger.add(sink, level="INFO")
