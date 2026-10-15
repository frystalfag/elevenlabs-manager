from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.models import ChunkStatus, Task, TaskState
from app.core.player import PlayerError, player
from app.ui.plan_dialog import ChunkTable
from app.utils.helpers import fmt_duration, fmt_int, open_path, reveal_in_folder

STATE_STYLE = {
    TaskState.RUNNING: "Warn", TaskState.DONE: "Ok", TaskState.FAILED: "Error",
    TaskState.PARTIAL: "Error", TaskState.PAUSED_AUTH: "Error",
}


class QueueView(QWidget):
    pause_clicked = pyqtSignal()
    resume_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    retry_failed_clicked = pyqtSignal()
    merge_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.task: Optional[Task] = None
        self.running = False
        self.active_account_id: Optional[str] = None
        lay = QVBoxLayout(self)

        self.title = QLabel(tr("No active task"))
        self.title.setObjectName("Title")
        lay.addWidget(self.title)
        self.state = QLabel("")
        lay.addWidget(self.state)
        self.progress = QProgressBar()
        lay.addWidget(self.progress)
        self.counts = QLabel("")
        lay.addWidget(self.counts)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("Muted")
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        self.pause_btn = QPushButton(tr("Pause (Esc)"))
        self.pause_btn.clicked.connect(self.pause_clicked.emit)
        self.resume_btn = QPushButton(tr("Continue"))
        self.resume_btn.setObjectName("Primary")
        self.resume_btn.clicked.connect(self.resume_clicked.emit)
        self.retry_btn = QPushButton(tr("Retry failed"))
        self.retry_btn.clicked.connect(self.retry_failed_clicked.emit)
        self.merge_btn = QPushButton(tr("Merge now"))
        self.merge_btn.setToolTip(tr("Merge the finished parts without waiting for the rest"))
        self.merge_btn.clicked.connect(self.merge_clicked.emit)
        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self.cancel_clicked.emit)
        for b in (self.pause_btn, self.resume_btn, self.retry_btn, self.merge_btn, self.cancel_btn):
            row.addWidget(b)
        row.addStretch(1)
        self.folder_btn = QPushButton(tr("Open folder"))
        self.folder_btn.clicked.connect(self._open_folder)
        self.play_btn = QPushButton(tr("▶ Play"))
        self.play_btn.clicked.connect(self._play)
        row.addWidget(self.folder_btn)
        row.addWidget(self.play_btn)
        lay.addLayout(row)

        self.table = ChunkTable()
        self.table.cellDoubleClicked.connect(self._play_part)
        lay.addWidget(self.table, 1)

        self._tick = QTimer(self, interval=1000)
        self._tick.timeout.connect(self._update_controls)
        self._tick.start()
        self.set_task(None)

    def set_task(self, task: Optional[Task]) -> None:
        self.task = task
        if task is None:
            self.title.setText(tr("No active task"))
            self.table.setRowCount(0)
            self.progress.setValue(0)
            self.counts.setText("")
            self.state.setText("")
            self.hint.setText(tr("Create a task on the 'Text' tab."))
        else:
            self.title.setText(f"«{task.title}»  ·  {task.settings.voice_name or task.settings.voice_id}  ·  "
                               f"{task.settings.model_id}")
            self.table.set_chunks(task.chunks)
            self.refresh()
        self._update_controls()

    def set_running(self, running: bool) -> None:
        self.running = running
        self._update_controls()

    def set_active_account(self, account_id: Optional[str]) -> None:
        self.active_account_id = account_id
        self._update_controls()

    def on_chunk_status(self, index: int, status: str) -> None:
        if self.task:
            ch = self.task.chunks[index - 1] if 0 < index <= self.task.total else None
            self.table.set_status(index, status, ch.error if ch else None)
            self.refresh()

    def refresh(self) -> None:
        t = self.task
        if t is None:
            return
        self.progress.setMaximum(max(1, t.total))
        self.progress.setValue(t.done_count)
        self.progress.setFormat(tr("{done} of {total} parts  (%p%)", done=t.done_count, total=t.total))
        failed = tr(" · errors: {n}", n=t.failed_count) if t.failed_count else ""
        left = sum(c.chars for c in t.pending_chunks)
        self.counts.setText(tr(
            "Done {done} of {total} parts{failed} · credits spent: {spent} · characters left: {left}",
            done=t.done_count, total=t.total, failed=failed, spent=fmt_int(t.credits_spent), left=fmt_int(left)))
        status = tr("Status: <b>{state}</b>", state=t.state.label)
        if t.pause_reason and t.state.is_paused:
            status += f" — {t.pause_reason}"
        self.state.setText(status)
        self.state.setObjectName(STATE_STYLE.get(t.state, ""))
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self._update_controls()

    def _retry_wait(self) -> float:
        if self.task and self.task.retry_after_until:
            return max(0.0, self.task.retry_after_until - time.time())
        return 0.0

    def _key_changed(self) -> bool:
        return bool(self.task and self.active_account_id and self.task.account_id
                    and self.active_account_id != self.task.account_id)

    def _update_controls(self) -> None:
        t = self.task
        has = t is not None
        resumable = has and not self.running and t.state.is_resumable and bool(t.pending_chunks or t.failed_count)
        wait = self._retry_wait()
        # after a 429 allow resume once retry-after is over or the key was switched
        can_resume = resumable and (wait <= 0 or self._key_changed())
        self.pause_btn.setEnabled(has and self.running)
        self.resume_btn.setEnabled(bool(can_resume))
        self.retry_btn.setVisible(has and t.failed_count > 0)
        self.retry_btn.setEnabled(has and not self.running and t.failed_count > 0)
        self.merge_btn.setEnabled(has and not self.running and t.done_count > 0 and t.state != TaskState.DONE)
        self.cancel_btn.setEnabled(has and t.state not in (TaskState.DONE, TaskState.CANCELLED))
        self.folder_btn.setEnabled(has and bool(t.output_dir))
        self.play_btn.setEnabled(has and bool(t.merged_file))
        if not has:
            return
        if wait > 0 and not self.running:
            extra = tr(" Key switched - you can continue right away.") if self._key_changed() else ""
            self.hint.setText(tr("The server asked to wait: {wait}.{extra}", wait=fmt_duration(wait), extra=extra))
        elif t.state == TaskState.PAUSED_LIMIT and not self.running:
            self.hint.setText(tr("Done {done} of {total} parts. Quota used up. Continue after the reset, "
                                 "or pick another key on the left and click 'Continue'.",
                                 done=t.done_count, total=t.total))
        elif t.state == TaskState.PAUSED_NETWORK and not self.running:
            self.hint.setText(tr("No connection. The app will offer to continue once the network is back."))
        elif t.state == TaskState.PAUSED_AUTH and not self.running:
            self.hint.setText(tr("The server rejected the key. Pick or add another key and click 'Continue'."))
        elif t.state == TaskState.PARTIAL:
            self.hint.setText(tr("Some parts failed because of server errors. Click 'Retry failed'."))
        elif t.state == TaskState.FAILED:
            self.hint.setText(tr("Error: {error}. Fix the settings/key and click 'Continue'.", error=t.error or '—'))
        elif t.state == TaskState.DONE:
            self.hint.setText(tr("Done! File: {file}", file=t.merged_file or t.output_dir))
        elif self.running:
            self.hint.setText(tr("Generating. You can minimize the window - the app will notify you when it's done."))
        else:
            self.hint.setText("")

    def _open_folder(self) -> None:
        if self.task and self.task.output_dir:
            target = Path(self.task.merged_file) if self.task.merged_file else Path(self.task.output_dir)
            reveal_in_folder(target)

    def _play(self) -> None:
        if self.task and self.task.merged_file:
            try:
                player.play(Path(self.task.merged_file))
            except PlayerError:
                open_path(Path(self.task.merged_file))

    def _play_part(self, row: int, _col: int) -> None:
        if not self.task or row >= self.task.total:
            return
        ch = self.task.chunks[row]
        if ch.file and ch.status in (ChunkStatus.DONE, ChunkStatus.CACHED):
            try:
                player.play(Path(ch.file))
            except PlayerError:
                open_path(Path(ch.file))
