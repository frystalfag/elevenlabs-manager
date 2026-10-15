from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from app.i18n import tr
from app.core import audio_merger
from app.core.account import Account
from app.core.cache import ChunkCache
from app.core.eleven_client import (
    ApiValidationError, ElevenClient, NetworkError, NotFound, PermissionDenied, QuotaExceeded,
    RateLimited, TransientError, Unauthorized,
)
from app.core.models import AppSettings, Chunk, ChunkStatus, Task, TaskState
from app.core.planner import chunk_credits
from app.core.queue import QueueStore, part_path
from app.core.storage import atomic_write_bytes
from app.utils.helpers import render_template, sanitize_filename

CONTEXT_CHARS = 400


class GenerationTask(QThread):
    progress = pyqtSignal(int, int)
    part_ready = pyqtSignal(int, object)
    chunk_status = pyqtSignal(int, str)
    paused = pyqtSignal(str)
    # not `finished`: QThread already has one
    task_finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    credits_used = pyqtSignal(str, int, int, float)
    account_checked = pyqtSignal(str, dict)
    key_invalid = pyqtSignal(str)

    def __init__(
        self,
        task: Task,
        queue: QueueStore,
        api_key: str,
        account: Account,
        cache: ChunkCache,
        app_settings: AppSettings,
        retry_failed: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        self.queue = queue
        self.api_key = api_key
        self.account = account
        self.cache = cache
        self.app_settings = app_settings.model_copy(deep=True)
        self.retry_failed = retry_failed
        self._pause_requested = False
        self._cancel_requested = False
        self._remaining: Optional[int] = None

    def request_pause(self) -> None:
        self._pause_requested = True

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _save(self) -> None:
        try:
            self.queue.save(self.task)
        except OSError as e:
            logger.error("Failed to save the queue: {}", e)

    def _set_chunk(self, chunk: Chunk, status: ChunkStatus, error: Optional[str] = None) -> None:
        chunk.status = status
        chunk.error = error
        self.chunk_status.emit(chunk.index, status.value)

    def _pause(self, reason: str, state: TaskState, message: str = "") -> None:
        self.task.state = state
        self.task.pause_reason = message or reason
        self._save()
        logger.info("Task paused ({}): {}/{} parts. {}", reason, self.task.done_count, self.task.total, message)
        self.paused.emit(reason)

    def _neighbors(self, chunk: Chunk) -> tuple[Optional[str], Optional[str]]:
        s = self.task.settings
        # v3 doesn't support previous_text/next_text
        if not s.use_context or s.model_id.startswith("eleven_v3"):
            return None, None
        idx = chunk.index - 1
        prev_text = self.task.chunks[idx - 1].text[-CONTEXT_CHARS:] if idx > 0 else None
        next_text = self.task.chunks[idx + 1].text[:CONTEXT_CHARS] if idx + 1 < self.task.total else None
        return prev_text, next_text

    def run(self) -> None:
        try:
            self._run()
        except Exception as e:
            logger.exception("Unexpected generation error")
            self.task.state = TaskState.FAILED
            self.task.error = str(e)
            self._save()
            self.failed.emit(tr("Unexpected error: {error}", error=e))

    def _run(self) -> None:
        t = self.task
        t.state = TaskState.RUNNING
        t.account_id = self.account.id
        t.pause_reason = None
        t.retry_after_until = None
        t.error = None
        self._save()
        client = ElevenClient(self.api_key)
        try:
            if not self._preflight(client):
                return
            self._loop(client)
        finally:
            client.close()

    def _preflight(self, client: ElevenClient) -> bool:
        try:
            sub = client.get_subscription()
            self.account_checked.emit(self.account.id, sub)
            limit = sub.get("character_limit")
            if limit is not None:
                self._remaining = max(0, int(limit) - int(sub.get("character_count") or 0))
        except PermissionDenied:
            logger.warning("Key has no user_read permission, remaining quota unknown, relying on API responses")
        except (Unauthorized, QuotaExceeded) as e:
            return self._handle_auth_or_quota(e)
        except NetworkError as e:
            self._pause("network", TaskState.PAUSED_NETWORK, e.message)
            return False
        except (TransientError, RateLimited, ApiValidationError) as e:
            logger.warning("Could not fetch remaining quota: {}", e.message)

        try:
            client.get_voice(self.task.settings.voice_id)
        except (NotFound, ApiValidationError) as e:
            self.task.state = TaskState.FAILED
            self.task.error = tr("Voice isn't available on this key: {error}", error=e.message)
            self._save()
            self.failed.emit(self.task.error)
            return False
        except Unauthorized as e:
            return self._handle_auth_or_quota(e)
        except Exception as e:
            logger.debug("Voice check skipped: {}", e)
        return True

    def _handle_auth_or_quota(self, e: Exception) -> bool:
        if isinstance(e, QuotaExceeded):
            self._pause("limit", TaskState.PAUSED_LIMIT, tr("The character quota on this key is used up"))
        else:
            self.key_invalid.emit(self.account.id)
            self._pause("auth", TaskState.PAUSED_AUTH, tr("Key is invalid, pick another key"))
        return False

    def _loop(self, client: ElevenClient) -> None:
        t = self.task
        s = t.settings
        voice_settings = s.voice_settings.model_dump()
        for chunk in t.chunks:
            if chunk.is_complete:
                continue
            if chunk.status == ChunkStatus.FAILED and not self.retry_failed:
                continue
            if self._cancel_requested:
                return
            # pause only between parts - an in-flight request is billed anyway
            if self._pause_requested:
                self._pause("user", TaskState.PAUSED_USER, tr("Paused by user"))
                return

            target = part_path(t, chunk)
            if self.cache.enabled and self.cache.get(chunk.hash, target):
                chunk.file = str(target)
                self._set_chunk(chunk, ChunkStatus.CACHED)
                self._save()
                self.part_ready.emit(chunk.index, target)
                self.progress.emit(t.done_count, t.total)
                continue

            cost = chunk_credits(chunk.chars, t.cost_multiplier)
            if self._remaining is not None and cost > self._remaining:
                self._pause("limit", TaskState.PAUSED_LIMIT,
                            tr("Quota used up: {left} parts left", left=t.total - t.done_count))
                return

            self._set_chunk(chunk, ChunkStatus.GENERATING)
            prev_text, next_text = self._neighbors(chunk)
            started = time.monotonic()
            try:
                result = client.tts(
                    chunk.text, s.voice_id, s.model_id, voice_settings, s.output_format,
                    s.language_code, s.seed, prev_text, next_text,
                )
            except QuotaExceeded:
                self._set_chunk(chunk, ChunkStatus.PENDING)
                self._pause("limit", TaskState.PAUSED_LIMIT, tr("The character quota on this key is used up"))
                return
            except RateLimited as e:
                self._set_chunk(chunk, ChunkStatus.PENDING)
                if e.retry_after:
                    t.retry_after_until = time.time() + e.retry_after
                self._pause("limit", TaskState.PAUSED_LIMIT, e.message)
                return
            except (Unauthorized, PermissionDenied) as e:
                self._set_chunk(chunk, ChunkStatus.PENDING)
                self._handle_auth_or_quota(e)
                return
            except NetworkError as e:
                self._set_chunk(chunk, ChunkStatus.PENDING)
                self._pause("network", TaskState.PAUSED_NETWORK, e.message)
                return
            except TransientError as e:
                self._set_chunk(chunk, ChunkStatus.FAILED, e.message)
                self._save()
                logger.error("Part {} failed: {}", chunk.index, e.message)
                continue
            # bad params usually break every part, no point continuing
            except (ApiValidationError, NotFound) as e:
                self._set_chunk(chunk, ChunkStatus.FAILED, e.message)
                t.state = TaskState.FAILED
                t.error = e.message
                self._save()
                self.failed.emit(e.message)
                return

            elapsed = time.monotonic() - started
            atomic_write_bytes(target, result.audio)
            spent = result.character_cost if result.character_cost is not None else cost
            chunk.file = str(target)
            chunk.credits = spent
            t.credits_spent += spent
            t.elapsed_seconds += elapsed
            if self._remaining is not None:
                self._remaining = max(0, self._remaining - spent)
            self._set_chunk(chunk, ChunkStatus.DONE)
            self._save()
            self.cache.put(chunk.hash, target)
            self.credits_used.emit(self.account.id, spent, chunk.chars, elapsed)
            self.part_ready.emit(chunk.index, target)
            self.progress.emit(t.done_count, t.total)
            logger.info("Part {}/{} done ({} credits, {:.1f} s)", chunk.index, t.total, spent, elapsed)

        self._finalize()

    def _finalize(self) -> None:
        t = self.task
        if t.failed_count:
            t.state = TaskState.PARTIAL
            t.error = tr("Parts that failed: {n}", n=t.failed_count)
            self._save()
            self.failed.emit(t.error + tr(". Click 'Retry failed'."))
            return
        merged: Optional[Path] = None
        if self.app_settings.auto_merge:
            try:
                merged = self._merge()
            except audio_merger.MergeError as e:
                logger.error("{}", e)
                t.error = str(e)
        t.state = TaskState.DONE
        t.merged_file = str(merged) if merged else None
        self._save()
        self.task_finished.emit(merged)

    def _merge(self) -> Path:
        t = self.task
        parts = [Path(c.file) for c in sorted(t.chunks, key=lambda c: c.index) if c.file]
        name = render_template(
            self.app_settings.filename_template,
            voice=sanitize_filename(t.settings.voice_name or t.settings.voice_id),
            model=t.settings.model_id,
            index=t.history_index,
            title=t.title,
        )
        out = audio_merger.unique_path(Path(t.output_dir) / f"{name}.mp3")
        merged = audio_merger.merge(
            parts, out, self.app_settings.merge_gap_ms, audio_merger.bitrate_from_format(t.settings.output_format),
        )
        if self.app_settings.delete_parts_after_merge:
            shutil.rmtree(Path(t.output_dir) / "parts", ignore_errors=True)
            for c in t.chunks:
                c.file = None
        return merged
