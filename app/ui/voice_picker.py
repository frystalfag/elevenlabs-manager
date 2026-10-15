from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.context import AppContext
from app.core.eleven_client import ElevenClient
from app.core.player import PlayerError, player
from app.ui.workers import error_text, run_async
from app.utils.paths import cache_dir

CATEGORIES = [(tr("All"), ""), (tr("Premade"), "premade"), (tr("Cloned"), "cloned"),
              (tr("Generated"), "generated"), (tr("Professional"), "professional")]


def voice_labels(v: dict) -> str:
    labels = v.get("labels") or {}
    parts = [str(labels.get(k)) for k in ("gender", "age", "accent", "use_case", "descriptive") if labels.get(k)]
    return ", ".join(parts)


class CloneDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Clone voice"))
        self.setMinimumWidth(480)
        self.files: list[Path] = []
        lay = QVBoxLayout(self)
        hint = QLabel(tr("Instant Voice Cloning: 1-25 clean recordings of one voice (mp3/wav), at least 1 minute "
                         "in total. Only clone your own voice or a voice whose owner agreed."))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        lay.addWidget(hint)
        form = QFormLayout()
        self.name = QLineEdit()
        self.desc = QLineEdit()
        self.noise = QCheckBox(tr("Remove background noise"))
        self.consent = QCheckBox(tr("I have the right to use this voice"))
        self.files_label = QLabel(tr("No files selected"))
        pick = QPushButton(tr("Choose files…"))
        pick.clicked.connect(self._pick)
        form.addRow(tr("Name:"), self.name)
        form.addRow(tr("Description:"), self.desc)
        form.addRow(pick, self.files_label)
        lay.addLayout(form)
        lay.addWidget(self.noise)
        lay.addWidget(self.consent)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _pick(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("Audio samples"), "", tr("Audio (*.mp3 *.wav *.m4a *.ogg *.flac)"))
        if paths:
            self.files = [Path(p) for p in paths][:25]
            self.files_label.setText(tr("Files selected: {n}", n=len(self.files)))

    def _accept(self) -> None:
        if not self.name.text().strip() or not self.files:
            QMessageBox.warning(self, tr("Cloning"), tr("Enter a name and pick at least one file."))
            return
        if not self.consent.isChecked():
            QMessageBox.warning(self, tr("Cloning"), tr("Confirm that you have the right to use the voice."))
            return
        self.accept()


class VoicePicker(QWidget):
    voice_selected = pyqtSignal(str, str)

    def __init__(self, ctx: AppContext, get_key: Callable[[], Optional[str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.get_key = get_key
        self.voices: list[dict] = ctx.load_cached("voices_cache.json")
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search by name, accent, description…"))
        self.search.textChanged.connect(self.render)
        self.category = QComboBox()
        for label, value in CATEGORIES:
            self.category.addItem(label, value)
        self.category.currentIndexChanged.connect(self.render)
        self.only_fav = QCheckBox(tr("★ Favorites"))
        self.only_fav.toggled.connect(self.render)
        top.addWidget(self.search, 1)
        top.addWidget(self.category)
        top.addWidget(self.only_fav)
        lay.addLayout(top)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._select())
        self.list.currentItemChanged.connect(lambda *_: self._update_info())
        lay.addWidget(self.list, 1)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        row = QHBoxLayout()
        self.select_btn = QPushButton(tr("Use this voice"))
        self.select_btn.setObjectName("Primary")
        self.select_btn.clicked.connect(self._select)
        preview = QPushButton(tr("▶ Preview"))
        preview.clicked.connect(self._preview)
        stop = QPushButton("■")
        stop.setToolTip(tr("Stop playback"))
        stop.clicked.connect(player.stop)
        fav = QPushButton(tr("★ Favorite"))
        fav.clicked.connect(self._toggle_fav)
        self.refresh_btn = QPushButton(tr("Refresh list"))
        self.refresh_btn.clicked.connect(self.refresh)
        clone = QPushButton(tr("Clone…"))
        clone.clicked.connect(self._clone)
        for w in (self.select_btn, preview, stop, fav, self.refresh_btn, clone):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        self.current_label = QLabel("")
        lay.addWidget(self.current_label)
        self.render()
        self.show_current()

    def find(self, voice_id: str) -> Optional[dict]:
        return next((v for v in self.voices if v.get("voice_id") == voice_id), None)

    def refresh(self) -> None:
        key = self.get_key()
        if not key:
            QMessageBox.information(self, tr("Voices"), tr("Add and select an active key first."))
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText(tr("Loading…"))

        def fetch() -> list[dict]:
            client = ElevenClient(key)
            try:
                return client.list_voices()
            finally:
                client.close()

        def ok(voices: list[dict]) -> None:
            self.voices = voices
            self.ctx.save_cached("voices_cache.json", voices)
            self._done_refresh()
            self.render()

        def err(e: Exception) -> None:
            self._done_refresh()
            QMessageBox.warning(self, tr("Voices"), tr("Failed to load voices: {error}", error=error_text(e)))

        run_async(self, fetch, ok, err)

    def _done_refresh(self) -> None:
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText(tr("Refresh list"))

    def render(self) -> None:
        query = self.search.text().strip().lower()
        cat = self.category.currentData()
        favs = self.ctx.favorites.ids
        current = self.ctx.settings.generation.voice_id
        self.list.clear()
        voices = sorted(self.voices, key=lambda v: (v.get("voice_id") not in favs, str(v.get("name", "")).lower()))
        for v in voices:
            vid = v.get("voice_id", "")
            if self.only_fav.isChecked() and vid not in favs:
                continue
            if cat and v.get("category") != cat:
                continue
            haystack = f"{v.get('name', '')} {voice_labels(v)} {v.get('description') or ''}".lower()
            if query and query not in haystack:
                continue
            star = "★ " if vid in favs else ""
            mark = "  ✓" if vid == current else ""
            item = QListWidgetItem(f"{star}{v.get('name', vid)}{mark}   —   {voice_labels(v)}")
            item.setData(Qt.ItemDataRole.UserRole, vid)
            self.list.addItem(item)
        if not self.voices:
            self.list.addItem(tr("The list is empty - click 'Refresh list' (needs an active key)."))

    def _current_voice(self) -> Optional[dict]:
        item = self.list.currentItem()
        vid = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self.find(vid) if vid else None

    def _update_info(self) -> None:
        v = self._current_voice()
        if not v:
            self.info.setText("")
            return
        models = ", ".join(v.get("high_quality_base_model_ids") or []) or "—"
        self.info.setText(tr("{name} · {category} · {labels}\n{description}\nHigh quality models: {models}",
                             name=v.get("name"), category=v.get("category", ""), labels=voice_labels(v),
                             description=(v.get("description") or "")[:200], models=models))

    def show_current(self) -> None:
        g = self.ctx.settings.generation
        self.current_label.setText(tr("Current voice: <b>{voice}</b>", voice=g.voice_name or tr('— not selected —')))

    def _select(self) -> None:
        v = self._current_voice()
        if not v:
            return
        self.voice_selected.emit(v["voice_id"], v.get("name", ""))
        self.show_current()
        self.render()

    def _toggle_fav(self) -> None:
        v = self._current_voice()
        if v:
            self.ctx.favorites.toggle(v["voice_id"])
            self.render()

    def _preview(self) -> None:
        v = self._current_voice()
        url = (v or {}).get("preview_url")
        if not url:
            QMessageBox.information(self, tr("Preview"), tr("This voice has no preview."))
            return
        target = cache_dir() / "previews" / (hashlib.sha1(url.encode()).hexdigest() + ".mp3")

        def play(_: object = None) -> None:
            try:
                player.play(target)
            except PlayerError as e:
                QMessageBox.warning(self, tr("Preview"), str(e))

        if target.is_file():
            play()
            return

        def download() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(ElevenClient(self.get_key() or "").download(url))

        run_async(self, download, play, lambda e: QMessageBox.warning(self, tr("Preview"), error_text(e)))

    def _clone(self) -> None:
        key = self.get_key()
        if not key:
            QMessageBox.information(self, tr("Cloning"), tr("You need an active key."))
            return
        acc = self.ctx.accounts.active if self.ctx.accounts else None
        if acc and acc.tier and not acc.can_clone:
            QMessageBox.warning(self, tr("Cloning"), tr("Cloning isn't available on the active key's plan."))
            return
        dlg = CloneDialog(self)
        if not dlg.exec():
            return
        name, desc, files, noise = dlg.name.text().strip(), dlg.desc.text().strip(), dlg.files, dlg.noise.isChecked()

        def ok(voice_id: str) -> None:
            QMessageBox.information(self, tr("Cloning"),
                                    tr("Voice '{name}' created (id {voice_id}).", name=name, voice_id=voice_id))
            self.refresh()

        run_async(self, lambda: ElevenClient(key).add_voice(name, files, desc, noise), ok,
                  lambda e: QMessageBox.warning(self, tr("Cloning"), error_text(e)))
