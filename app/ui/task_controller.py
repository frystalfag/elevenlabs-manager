from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from loguru import logger
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from app.i18n import tr
from app.core import audio_merger
from app.core.context import AppContext
from app.core.eleven_client import ElevenClient, check_connectivity
from app.core.manager import GenerationTask
from app.core.models import Task, TaskState
from app.core.planner import build_plan
from app.core.player import PlayerError, player
from app.core.queue import create_task, reset_failed
from app.ui.dialogs import show_ffmpeg_dialog
from app.ui.plan_dialog import PlanDialog
from app.ui.workers import error_text, run_async
from app.utils.helpers import fmt_int, open_path, reveal_in_folder

TEST_PHRASE = tr("This is a voice test. This is how your text will sound.")


class TaskController(QObject):
    task_changed = pyqtSignal(object)
    chunk_status = pyqtSignal(int, str)
    status_changed = pyqtSignal(str, str)
    running_changed = pyqtSignal(bool)
    accounts_changed = pyqtSignal()
    notify = pyqtSignal(str, str, object, bool)

    def __init__(self, ctx: AppContext, window: QWidget) -> None:
        super().__init__(window)
        self.ctx = ctx
        self.window = window
        self.task: Optional[Task] = ctx.queue.load()
        self.thread: Optional[GenerationTask] = None
        self._network_prompted = False
        self._cancel_pending = False
        self._net_timer = QTimer(self, interval=15000)
        self._net_timer.timeout.connect(self._check_network)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.isRunning()

    def _active(self) -> tuple[Optional[object], Optional[str]]:
        acc = self.ctx.accounts.active if self.ctx.accounts else None
        key = self.ctx.accounts.api_key(acc.id) if acc else None
        return acc, key

    def _emit_status(self) -> None:
        t = self.task
        if self.running:
            text = tr("generating {done}/{total}", done=t.done_count, total=t.total) if t else tr("generating")
            self.status_changed.emit("running", text)
        elif t and t.state.is_paused:
            self.status_changed.emit("paused", t.state.label)
        elif t and t.state in (TaskState.FAILED, TaskState.PARTIAL):
            self.status_changed.emit("error", t.state.label)
        else:
            self.status_changed.emit("idle", tr("idle"))

    def _preflight_ui(self) -> bool:
        if not audio_merger.ffmpeg_available():
            show_ffmpeg_dialog(self.window)
            return False
        acc, key = self._active()
        if not acc or not key:
            QMessageBox.warning(self.window, tr("Key"), tr("No active key. Add a key in the left panel."))
            return False
        return True

    def start(self, text: str) -> None:
        if self.running:
            QMessageBox.information(self.window, tr("Generation"), tr("A task is already running."))
            return
        if not text.strip():
            QMessageBox.information(self.window, tr("Generation"), tr("Enter some text."))
            return
        if not self._preflight_ui():
            return
        g = self.ctx.settings.generation
        if not g.voice_id:
            QMessageBox.warning(self.window, tr("Voice"), tr("Pick a voice on the 'Voice' tab."))
            return
        server_models = self.ctx.load_cached("models_cache.json")
        if server_models and g.model_id not in {m.get("model_id") for m in server_models}:
            QMessageBox.warning(self.window, tr("Model"), tr(
                "Model {model} isn't available on your plan (not in the server list). Pick another one.",
                model=g.model_id))
            return
        if self.task and self.task.state.is_resumable and self.task.pending_chunks:
            answer = QMessageBox.question(
                self.window, tr("Unfinished task"),
                tr("There's an unfinished task '{title}' ({done}/{total}). Cancel it and start a new one? "
                   "Finished parts stay in the folder.",
                   title=self.task.title, done=self.task.done_count, total=self.task.total))
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._finish_cancel()
        acc, _key = self._active()
        plan = build_plan(text, g, acc, chunk_size=self.ctx.settings.chunk_size,
                          model_info=self.ctx.model_info(g.model_id), sec_per_kchar=self.ctx.usage.sec_per_kchar,
                          cached_hashes=self.ctx.cache.known_hashes())
        if not plan.chunks:
            return
        dlg = PlanDialog(plan, g.voice_name or g.voice_id, g.model_name or g.model_id, self.window)
        if not dlg.exec():
            return
        try:
            task = create_task(text, plan.chunks, g, self.ctx.output_root, plan.cost_multiplier,
                               self.ctx.history.next_index())
            self.ctx.queue.save(task)
        except OSError as e:
            QMessageBox.critical(self.window, tr("Error"), tr("Failed to create the task folder: {error}", error=e))
            return
        self.task = task
        self.task_changed.emit(task)
        logger.info("New task \"{}\": {} parts, {} chars", task.title, task.total, task.total_chars)
        self._launch()

    def resume(self, retry_failed: bool = False) -> None:
        if self.running or self.task is None:
            return
        if not self._preflight_ui():
            return
        if retry_failed:
            reset_failed(self.task)
            self.ctx.queue.save(self.task)
            self.task_changed.emit(self.task)
        self._launch(retry_failed)

    def _launch(self, retry_failed: bool = False) -> None:
        acc, key = self._active()
        assert self.task is not None and acc is not None and key is not None
        self._network_prompted = False
        self._net_timer.stop()
        th = GenerationTask(self.task, self.ctx.queue, key, acc, self.ctx.cache, self.ctx.settings,
                            retry_failed=retry_failed)
        th.progress.connect(lambda *_: self._emit_status())
        th.chunk_status.connect(self._on_chunk_status)
        th.paused.connect(self._on_paused)
        th.task_finished.connect(self._on_finished)
        th.failed.connect(self._on_failed)
        th.credits_used.connect(self._on_credits)
        th.account_checked.connect(self._on_account_checked)
        th.key_invalid.connect(self._on_key_invalid)
        th.finished.connect(self._on_thread_done)
        self.thread = th
        th.start()
        self.running_changed.emit(True)
        self._emit_status()

    def pause(self) -> None:
        if self.running and self.thread:
            self.thread.request_pause()
            logger.info("Pause requested, the current part will finish first")

    def cancel(self) -> None:
        if self.task is None:
            return
        spent = self.task.credits_spent
        msg = tr("Cancel the task?")
        if spent:
            msg = tr("Already spent {spent} credits ({done} parts done). Cancel the task? "
                     "Finished parts stay in the folder.",
                     spent=fmt_int(spent), done=self.task.done_count)
        if QMessageBox.question(self.window, tr("Cancel"), msg) != QMessageBox.StandardButton.Yes:
            return
        if self.running and self.thread:
            # don't block the ui, finish the cancel when the thread exits
            self._cancel_pending = True
            self.thread.request_cancel()
            logger.info("Cancel requested, waiting for the current part")
            return
        self._finish_cancel()

    def _finish_cancel(self) -> None:
        if self.task:
            self.task.state = TaskState.CANCELLED
            self.ctx.history.add_from_task(self.task)
            logger.info("Task \"{}\" cancelled", self.task.title)
        self.ctx.queue.clear()
        self.task = None
        self.task_changed.emit(None)
        self._emit_status()

    def defer(self) -> None:
        self._emit_status()

    def merge_now(self) -> None:
        t = self.task
        if not t or self.running:
            return
        if not audio_merger.ffmpeg_available():
            show_ffmpeg_dialog(self.window)
            return
        parts = [Path(c.file) for c in t.chunks if c.is_complete and c.file]
        out = audio_merger.unique_path(Path(t.output_dir) / f"partial_{t.done_count}_of_{t.total}.mp3")

        def ok(path: Path) -> None:
            self.notify.emit(tr("Merged"), tr("Finished parts ({n}) merged", n=len(parts)), path, False)
            reveal_in_folder(path)

        run_async(self.window, lambda: audio_merger.merge(parts, out, self.ctx.settings.merge_gap_ms), ok,
                  lambda e: QMessageBox.warning(self.window, tr("Merge"), error_text(e)))

    def test_voice(self) -> None:
        acc, key = self._active()
        g = self.ctx.settings.generation
        if not key or not g.voice_id:
            QMessageBox.information(self.window, tr("Test"), tr("You need an active key and a selected voice."))
            return
        target = self.ctx.output_root / "_test.mp3"

        def gen() -> Path:
            client = ElevenClient(key)
            try:
                res = client.tts(TEST_PHRASE, g.voice_id, g.model_id, g.voice_settings.model_dump(),
                                 g.output_format, g.language_code, g.seed)
            finally:
                client.close()
            target.write_bytes(res.audio)
            return target

        def ok(path: Path) -> None:
            self.ctx.usage.record(acc.id, len(TEST_PHRASE), len(TEST_PHRASE), 0)
            try:
                player.play(path)
            except PlayerError:
                open_path(path)

        logger.info("Voice test ({} chars)", len(TEST_PHRASE))
        run_async(self.window, gen, ok, lambda e: QMessageBox.warning(self.window, tr("Test"), error_text(e)))

    def _on_chunk_status(self, index: int, status: str) -> None:
        self.chunk_status.emit(index, status)
        self._emit_status()

    def _on_paused(self, reason: str) -> None:
        t = self.task
        assert t is not None
        left = t.total - t.done_count
        if reason == "limit":
            wait = ""
            if t.retry_after_until:
                wait = tr(" Retry possible in {sec} s.", sec=int(max(0, t.retry_after_until - time.time())))
            self.notify.emit(tr("Quota used up"),
                             tr("Done {done} of {total} parts, {left} left.{wait} "
                                "Continue after the quota resets or after switching keys.",
                                done=t.done_count, total=t.total, left=left, wait=wait),
                             None, True)
        elif reason == "network":
            self.notify.emit(tr("No network"),
                             tr("Generation paused. I'll continue after you confirm, once the connection is back."),
                             None, True)
            self._net_timer.start()
        elif reason == "auth":
            self.notify.emit(tr("Key is invalid"), tr("Pick another key and click 'Continue'."), None, True)

    def _on_finished(self, merged: Optional[Path]) -> None:
        t = self.task
        assert t is not None
        self.ctx.history.add_from_task(t)
        self.ctx.queue.clear()
        target = merged or Path(t.output_dir)
        msg = tr("'{title}': {parts} parts, {credits} credits.",
                 title=t.title, parts=t.total, credits=fmt_int(t.credits_spent))
        if t.error:
            msg += f"\n{t.error}"
        self.notify.emit(tr("Done"), msg, target, False)
        self._ask_open(target)

    def _ask_open(self, target: Path) -> None:
        box = QMessageBox(self.window)
        box.setWindowTitle(tr("Done"))
        box.setText(tr("Voiceover is ready:\n{file}", file=target))
        open_btn = box.addButton(tr("Open file"), QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton(tr("Show in folder"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(tr("Close"), QMessageBox.ButtonRole.RejectRole)
        box.setModal(False)
        box.buttonClicked.connect(lambda b: open_path(target) if b is open_btn else
                                  reveal_in_folder(target) if b is folder_btn else None)
        box.show()

    def _on_failed(self, message: str) -> None:
        self.notify.emit(tr("Generation error"), message, None, True)

    def _on_credits(self, account_id: str, credits: int, chars: int, seconds: float) -> None:
        self.ctx.usage.record(account_id, credits, chars, seconds)
        acc = self.ctx.accounts.get(account_id) if self.ctx.accounts else None
        if acc:
            acc.spend_locally(credits)
            self.ctx.accounts.save()
        self.accounts_changed.emit()

    def _on_account_checked(self, account_id: str, sub: dict) -> None:
        acc = self.ctx.accounts.get(account_id) if self.ctx.accounts else None
        if acc:
            acc.apply_subscription(sub)
            self.ctx.accounts.save()
            self.accounts_changed.emit()

    def _on_key_invalid(self, account_id: str) -> None:
        if self.ctx.accounts:
            self.ctx.accounts.mark_invalid(account_id)
        self.accounts_changed.emit()

    def _on_thread_done(self) -> None:
        self.thread = None
        self.running_changed.emit(False)
        if self._cancel_pending:
            self._cancel_pending = False
            self._finish_cancel()
            return
        self.task_changed.emit(self.task)
        self._emit_status()

    def _check_network(self) -> None:
        if not self.task or self.task.state != TaskState.PAUSED_NETWORK or self.running:
            self._net_timer.stop()
            return

        def ok(online: bool) -> None:
            if not online or self._network_prompted or self.running:
                return
            self._network_prompted = True
            self._net_timer.stop()
            if QMessageBox.question(self.window, tr("Network is back"),
                                    tr("Connection to ElevenLabs is back. Continue generating?")) == \
                    QMessageBox.StandardButton.Yes:
                self.resume()
            else:
                self._network_prompted = False

        run_async(self, check_connectivity, ok)

    def shutdown(self) -> None:
        if self.running and self.thread:
            self.thread.request_pause()
            self.thread.wait(90_000)
