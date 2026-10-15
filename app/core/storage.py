from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from app.utils.paths import backups_dir, data_dir

T = TypeVar("T", bound=BaseModel)

_write_lock = threading.RLock()


# temp file + os.replace, so a crash never leaves a half-written queue.json
def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            # antivirus likes to grab fresh files on windows for a moment
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def atomic_write_json(path: Path, data: Any) -> None:
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(path, raw)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        broken = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        logger.error("{} is corrupted: {}. Keeping a copy as {}", path.name, e, broken.name)
        try:
            path.replace(broken)
        except OSError:
            pass
        return default
    except OSError as e:
        logger.error("Failed to read {}: {}", path, e)
        return default


def load_model(path: Path, cls: type[T], factory: Callable[[], T]) -> T:
    raw = read_json(path, None)
    if raw is None:
        return factory()
    try:
        return cls.model_validate(raw)
    except ValidationError as e:
        logger.warning("Bad data in {}, using defaults: {}", path.name, e)
        return factory()


def save_model(path: Path, model: BaseModel) -> None:
    atomic_write_json(path, model.model_dump(mode="json"))


class JsonList:
    def __init__(self, path: Path, cls: type[T], limit: int | None = None) -> None:
        self.path = path
        self.cls = cls
        self.limit = limit
        self.items: list[Any] = []
        self.load()

    def load(self) -> None:
        raw = read_json(self.path, [])
        items: list[Any] = []
        for row in raw if isinstance(raw, list) else []:
            try:
                items.append(self.cls.model_validate(row))
            except ValidationError:
                logger.warning("Skipped a broken record in {}", self.path.name)
        self.items = items

    def save(self) -> None:
        if self.limit is not None and len(self.items) > self.limit:
            self.items = self.items[-self.limit:]
        atomic_write_json(self.path, [i.model_dump(mode="json") for i in self.items])


BACKUP_KEEP = 7
_BACKUP_FILES = (
    "accounts.json", "accounts.enc", "security.json", "usage.json", "presets.json",
    "queue.json", "history.json", "favorites.json", "settings.json", "draft.txt",
)


def daily_backup(last_backup_date: str) -> str:
    today = date.today().isoformat()
    if last_backup_date == today:
        return last_backup_date
    target = backups_dir() / f"backup_{datetime.now():%Y%m%d_%H%M%S}.zip"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in _BACKUP_FILES:
                src = data_dir() / name
                if src.exists():
                    zf.write(src, arcname=name)
        backups = sorted(backups_dir().glob("backup_*.zip"))
        for old in backups[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)
        logger.info("Backup created: {}", target.name)
        return today
    except OSError as e:
        logger.error("Backup failed: {}", e)
        return last_backup_date
