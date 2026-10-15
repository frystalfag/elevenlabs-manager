from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from app.core.models import Chunk, ChunkStatus, GenerationSettings, Task, TaskState
from app.core.storage import atomic_write_json, read_json
from app.utils.helpers import now_iso, text_title
from app.utils.paths import data_file


class QueueStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_file("queue.json")
        self._lock = threading.Lock()

    def load(self) -> Optional[Task]:
        raw = read_json(self.path, None)
        if not raw:
            return None
        try:
            task = Task.model_validate(raw)
        except ValidationError as e:
            logger.error("queue.json is unreadable, task lost: {}", e)
            return None
        for ch in task.chunks:
            # app died mid-request
            if ch.status == ChunkStatus.GENERATING:
                ch.status = ChunkStatus.PENDING
            if ch.is_complete and ch.file and not Path(ch.file).is_file():
                logger.warning("File for part {} is gone, it will be generated again", ch.index)
                ch.status = ChunkStatus.PENDING
                ch.file = None
        return task

    def save(self, task: Task) -> None:
        with self._lock:
            task.updated_at = now_iso()
            atomic_write_json(self.path, task.model_dump(mode="json"))

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as e:
                logger.error("Failed to clear the queue: {}", e)


def create_task(
    text: str,
    chunks: list[Chunk],
    settings: GenerationSettings,
    output_root: Path,
    cost_multiplier: float,
    history_index: int,
) -> Task:
    task = Task(
        title=text_title(text),
        settings=settings.model_copy(deep=True),
        chunks=[c.model_copy(deep=True) for c in chunks],
        state=TaskState.PAUSED_USER,
        cost_multiplier=cost_multiplier,
        history_index=history_index,
    )
    folder = output_root / f"{datetime.now():%Y-%m-%d_%H%M%S}_{task.id[:6]}"
    (folder / "parts").mkdir(parents=True, exist_ok=True)
    (folder / "source.txt").write_text(text, encoding="utf-8")
    task.output_dir = str(folder)
    return task


def part_path(task: Task, chunk: Chunk) -> Path:
    width = max(2, len(str(task.total)))
    return Path(task.output_dir) / "parts" / f"part_{chunk.index:0{width}d}.mp3"


def reset_failed(task: Task) -> int:
    count = 0
    for ch in task.chunks:
        if ch.status == ChunkStatus.FAILED:
            ch.status = ChunkStatus.PENDING
            ch.error = None
            count += 1
    return count
