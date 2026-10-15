from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.i18n import tr
from app.utils.helpers import now_iso


class ChunkStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    DONE = "done"
    CACHED = "cached"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            "pending": tr("waiting"),
            "generating": tr("generating"),
            "done": tr("done"),
            "cached": tr("from cache"),
            "failed": tr("error"),
        }[self.value]


class TaskState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED_LIMIT = "PAUSED_LIMIT"
    PAUSED_USER = "PAUSED_USER"
    PAUSED_NETWORK = "PAUSED_NETWORK"
    PAUSED_AUTH = "PAUSED_AUTH"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"

    @property
    def is_paused(self) -> bool:
        return self.value.startswith("PAUSED")

    @property
    def is_resumable(self) -> bool:
        return self.is_paused or self in (TaskState.RUNNING, TaskState.FAILED, TaskState.PARTIAL)

    @property
    def label(self) -> str:
        return {
            "RUNNING": tr("Generating"),
            "PAUSED_LIMIT": tr("Paused: quota used up"),
            "PAUSED_USER": tr("Paused (user)"),
            "PAUSED_NETWORK": tr("Paused: no network"),
            "PAUSED_AUTH": tr("Paused: key invalid"),
            "PARTIAL": tr("Done with errors"),
            "FAILED": tr("Error"),
            "DONE": tr("Done"),
            "CANCELLED": tr("Cancelled"),
        }[self.value]


class VoiceSettings(BaseModel):
    stability: float = Field(0.5, ge=0.0, le=1.0)
    similarity_boost: float = Field(0.75, ge=0.0, le=1.0)
    style: float = Field(0.0, ge=0.0, le=1.0)
    use_speaker_boost: bool = True
    speed: float = Field(1.0, ge=0.7, le=1.2)


class GenerationSettings(BaseModel):
    voice_id: str = ""
    voice_name: str = ""
    model_id: str = "eleven_multilingual_v2"
    model_name: str = ""
    output_format: str = "mp3_44100_128"
    language_code: str = ""
    seed: Optional[int] = None
    use_context: bool = True
    voice_settings: VoiceSettings = Field(default_factory=VoiceSettings)

    def fingerprint(self) -> str:
        payload = {
            "voice_id": self.voice_id,
            "model_id": self.model_id,
            "output_format": self.output_format,
            "language_code": self.language_code,
            "seed": self.seed,
            "voice_settings": self.voice_settings.model_dump(),
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def chunk_hash(text: str, salt: str) -> str:
    return hashlib.sha256((text + "\x00" + salt).encode("utf-8")).hexdigest()


class Chunk(BaseModel):
    index: int
    text: str
    start: int
    end: int
    hash: str = ""
    status: ChunkStatus = ChunkStatus.PENDING
    file: Optional[str] = None
    error: Optional[str] = None
    credits: int = 0

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def is_complete(self) -> bool:
        return self.status in (ChunkStatus.DONE, ChunkStatus.CACHED)


class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    settings: GenerationSettings
    chunks: list[Chunk] = Field(default_factory=list)
    state: TaskState = TaskState.RUNNING
    pause_reason: Optional[str] = None
    retry_after_until: Optional[float] = None
    account_id: Optional[str] = None
    output_dir: str = ""
    merged_file: Optional[str] = None
    credits_spent: int = 0
    cost_multiplier: float = 1.0
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    history_index: int = 1

    @property
    def total(self) -> int:
        return len(self.chunks)

    @property
    def done_count(self) -> int:
        return sum(1 for c in self.chunks if c.is_complete)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.chunks if c.status == ChunkStatus.FAILED)

    @property
    def pending_chunks(self) -> list[Chunk]:
        return [c for c in self.chunks if not c.is_complete and c.status != ChunkStatus.FAILED]

    @property
    def total_chars(self) -> int:
        return sum(c.chars for c in self.chunks)


class Preset(BaseModel):
    name: str
    settings: GenerationSettings
    created_at: str = Field(default_factory=now_iso)


class HistoryEntry(BaseModel):
    id: str
    created_at: str = Field(default_factory=now_iso)
    title: str = ""
    voice_id: str = ""
    voice_name: str = ""
    model_id: str = ""
    chars: int = 0
    credits: int = 0
    parts: int = 0
    state: str = ""
    output_dir: str = ""
    merged_file: Optional[str] = None
    source_file: Optional[str] = None
    seconds: float = 0.0
    settings: Optional[GenerationSettings] = None


class AppSettings(BaseModel):
    theme: Literal["auto", "dark", "light"] = "dark"
    language: Literal["en", "ru"] = "en"
    auto_merge: bool = True
    delete_parts_after_merge: bool = False
    cache_enabled: bool = True
    filename_template: str = "{date}_{voice}_{index}"
    output_dir: str = ""
    chunk_size: int = Field(2500, ge=200, le=40000)
    merge_gap_ms: int = Field(250, ge=0, le=5000)
    auto_lock_minutes: int = Field(0, ge=0, le=600)
    minimize_to_tray: bool = True
    check_updates: bool = False
    update_repo: str = ""
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    last_backup_date: str = ""
