from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from loguru import logger
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.context import AppContext
from app.core.planner import Plan, build_plan, plan_summary
from app.utils.helpers import fmt_int

MAX_TEXT_CHARS = 2_000_000


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class DropTextEdit(QPlainTextEdit):
    file_dropped = pyqtSignal(Path)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dropEvent(self, e: QDropEvent) -> None:
        urls = [u for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".txt")]
        if urls:
            self.file_dropped.emit(Path(urls[0].toLocalFile()))
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class TextPanel(QWidget):
    generate_requested = pyqtSignal()
    test_requested = pyqtSignal()
    preset_applied = pyqtSignal()

    def __init__(self, ctx: AppContext, sec_per_kchar: Callable[[], Optional[float]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.sec_per_kchar = sec_per_kchar
        self.plan: Optional[Plan] = None
        self._draft_dirty = False
        lay = QVBoxLayout(self)

        presets = QHBoxLayout()
        presets.addWidget(QLabel(tr("Preset:")))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(200)
        presets.addWidget(self.preset_combo, 1)
        apply_btn = QPushButton(tr("Apply"))
        apply_btn.clicked.connect(self.apply_preset)
        save_btn = QPushButton(tr("Save current as…"))
        save_btn.clicked.connect(self.save_preset)
        del_btn = QPushButton(tr("Delete"))
        del_btn.clicked.connect(self.delete_preset)
        for b in (apply_btn, save_btn, del_btn):
            presets.addWidget(b)
        lay.addLayout(presets)

        self.edit = DropTextEdit()
        self.edit.setPlaceholderText(tr("Paste text or drop a .txt file here…"))
        self.edit.textChanged.connect(self._text_changed)
        self.edit.file_dropped.connect(self.load_file)
        lay.addWidget(self.edit, 1)

        self.counter = QLabel(tr("0 characters"))
        self.counter.setObjectName("Muted")
        lay.addWidget(self.counter)
        self.estimate = QLabel("")
        self.estimate.setWordWrap(True)
        self.estimate.setObjectName("Panel")
        self.estimate.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(self.estimate)

        buttons = QHBoxLayout()
        open_btn = QPushButton(tr("Open .txt…"))
        open_btn.clicked.connect(self._open_dialog)
        clear_btn = QPushButton(tr("Clear"))
        clear_btn.clicked.connect(self._clear)
        self.test_btn = QPushButton(tr("Test (~45 credits)"))
        self.test_btn.setToolTip(tr("Voice a short phrase with the current settings"))
        self.test_btn.clicked.connect(self.test_requested.emit)
        self.gen_btn = QPushButton(tr("Generate  (Ctrl+Enter)"))
        self.gen_btn.setObjectName("Primary")
        self.gen_btn.clicked.connect(self.generate_requested.emit)
        for b in (open_btn, clear_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.test_btn)
        buttons.addWidget(self.gen_btn)
        lay.addLayout(buttons)

        self._estimate_timer = QTimer(self, singleShot=True, interval=400)
        self._estimate_timer.timeout.connect(self.update_estimate)
        self._draft_timer = QTimer(self, interval=5000)
        self._draft_timer.timeout.connect(self._save_draft)
        self._draft_timer.start()

        self.reload_presets()
        self.edit.setPlainText(ctx.load_draft())

    def text(self) -> str:
        return self.edit.toPlainText()

    def set_text(self, text: str) -> None:
        self.edit.setPlainText(text)

    def _text_changed(self) -> None:
        self._draft_dirty = True
        n = len(self.text())
        self.counter.setText(tr("{n} characters", n=fmt_int(n)))
        if n > MAX_TEXT_CHARS:
            self.counter.setText(tr("{n} characters - too many (max {max})", n=fmt_int(n), max=fmt_int(MAX_TEXT_CHARS)))
        self._estimate_timer.start()

    def _save_draft(self) -> None:
        if not self._draft_dirty:
            return
        try:
            self.ctx.save_draft(self.text())
            self._draft_dirty = False
        except OSError as e:
            logger.warning("Draft not saved: {}", e)

    def flush_draft(self) -> None:
        self._draft_dirty = True
        self._save_draft()

    def load_file(self, path: Path) -> None:
        try:
            text = read_text_file(path)
        except OSError as e:
            QMessageBox.warning(self, tr("File"), tr("Can't read {name}: {error}", name=path.name, error=e))
            return
        if len(text) > MAX_TEXT_CHARS:
            QMessageBox.warning(self, tr("File"), tr("The file is too large for one task, split it up."))
            return
        self.set_text(text)
        logger.info("Loaded text from {} ({} chars)", path.name, len(text))

    def _open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Open text"), "", tr("Text (*.txt);;All files (*)"))
        if path:
            self.load_file(Path(path))

    def _clear(self) -> None:
        if not self.text():
            return
        if QMessageBox.question(self, tr("Clear"), tr("Clear the text?")) != QMessageBox.StandardButton.Yes:
            return
        self.edit.clear()

    def update_estimate(self) -> None:
        text = self.text()
        if not text.strip():
            self.plan = None
            self.estimate.setText(tr("The estimate will show up once you enter some text."))
            return
        g = self.ctx.settings.generation
        account = self.ctx.accounts.active if self.ctx.accounts else None
        try:
            self.plan = build_plan(
                text, g, account,
                chunk_size=self.ctx.settings.chunk_size,
                model_info=self.ctx.model_info(g.model_id),
                sec_per_kchar=self.sec_per_kchar(),
                cached_hashes=self.ctx.cache.known_hashes(),
            )
        except Exception as e:
            logger.exception("Estimate failed")
            self.estimate.setText(tr("Couldn't calculate the estimate: {error}", error=e))
            return
        summary = plan_summary(self.plan)
        if not g.voice_id:
            summary += tr("\n⚠ No voice selected - see the 'Voice' tab.")
        if account is None:
            summary += tr("\n⚠ No active key - add one in the left panel.")
        if self.plan.cached_parts:
            summary += tr("\nℹ Some parts will come from the cache: same text, voice, model and settings. "
                          "You can turn the cache off in Preferences.")
        self.estimate.setText(summary)
        self.counter.setText(tr("{n} characters · {parts} parts of ≤{size}", n=fmt_int(len(text)),
                                parts=self.plan.parts_total, size=fmt_int(self.plan.max_chunk_chars)))

    def reload_presets(self) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItem(tr("— pick a preset —"), "")
        for name in self.ctx.presets.names():
            self.preset_combo.addItem(name, name)

    def focus_presets(self) -> None:
        self.preset_combo.setFocus()
        self.preset_combo.showPopup()

    def apply_preset(self) -> None:
        name = self.preset_combo.currentData()
        preset = self.ctx.presets.get(name) if name else None
        if not preset:
            return
        self.ctx.settings.generation = preset.settings.model_copy(deep=True)
        self.ctx.save_settings()
        self.preset_applied.emit()
        self.update_estimate()
        logger.info("Applied preset \"{}\"", name)

    def save_preset(self) -> None:
        current = self.preset_combo.currentData() or ""
        name, ok = QInputDialog.getText(self, tr("Save preset"),
                                        tr("Preset name (voice, model and all settings):"), text=current)
        if ok and name.strip():
            self.ctx.presets.upsert(name.strip(), self.ctx.settings.generation)
            self.reload_presets()
            self.preset_combo.setCurrentIndex(self.preset_combo.findData(name.strip()))

    def delete_preset(self) -> None:
        name = self.preset_combo.currentData()
        if not name:
            return
        answer = QMessageBox.question(self, tr("Preset"), tr("Delete preset '{name}'?", name=name))
        if answer == QMessageBox.StandardButton.Yes:
            self.ctx.presets.remove(name)
            self.reload_presets()

    def set_busy(self, busy: bool) -> None:
        self.gen_btn.setEnabled(not busy)
        self.test_btn.setEnabled(not busy)
