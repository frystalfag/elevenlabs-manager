from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.context import AppContext
from app.core.models import HistoryEntry
from app.core.player import PlayerError, player
from app.utils.helpers import fmt_int, open_path, reveal_in_folder


class HistoryView(QWidget):
    repeat_requested = pyqtSignal(object)

    def __init__(self, ctx: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[HistoryEntry] = []
        lay = QVBoxLayout(self)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search by title…"))
        self.search.textChanged.connect(self.render)
        lay.addWidget(self.search)
        filters = QHBoxLayout()
        self.voice = QComboBox()
        self.voice.currentIndexChanged.connect(self.render)
        self.since = QDateEdit()
        self.since.setCalendarPopup(True)
        self.since.setDisplayFormat("dd.MM.yyyy")
        self.since.setDate(QDate.currentDate().addDays(-90))
        self.since.dateChanged.connect(self.render)
        filters.addWidget(self.voice, 1)
        filters.addWidget(self.since)
        lay.addLayout(filters)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Date"), tr("Title"), tr("Voice"), tr("Chars")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open_folder(r))
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        repeat = QPushButton(tr("Repeat"))
        repeat.setToolTip(tr("Load the text and settings of this generation"))
        repeat.clicked.connect(self._repeat)
        folder = QPushButton(tr("Open folder"))
        folder.clicked.connect(lambda: self._open_folder(self.table.currentRow()))
        play = QPushButton("▶")
        play.clicked.connect(self._play)
        export = QPushButton(tr("Export…"))
        export.clicked.connect(self._export)
        for b in (repeat, folder, play, export):
            row.addWidget(b)
        lay.addLayout(row)
        self.render()

    def render(self) -> None:
        items = list(reversed(self.ctx.history.items))
        voices = sorted({e.voice_name or e.voice_id for e in items if e.voice_id})
        current_voice = self.voice.currentData()
        self.voice.blockSignals(True)
        self.voice.clear()
        self.voice.addItem(tr("All voices"), "")
        for v in voices:
            self.voice.addItem(v, v)
        self.voice.setCurrentIndex(max(0, self.voice.findData(current_voice)))
        self.voice.blockSignals(False)

        query = self.search.text().strip().lower()
        voice = self.voice.currentData()
        since = datetime.combine(self.since.date().toPyDate(), datetime.min.time())
        self.rows = []
        for e in items:
            try:
                created = datetime.fromisoformat(e.created_at)
            except ValueError:
                created = datetime.now() - timedelta(days=9999)
            if created < since:
                continue
            if voice and (e.voice_name or e.voice_id) != voice:
                continue
            if query and query not in e.title.lower():
                continue
            self.rows.append(e)
        self.table.setRowCount(len(self.rows))
        for r, e in enumerate(self.rows):
            date_text = e.created_at.replace("T", " ")[:16]
            mark = "" if e.state in ("DONE", "") else f" [{e.state}]"
            cells = [date_text, e.title + mark, e.voice_name or e.voice_id, fmt_int(e.chars)]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 1:
                    item.setToolTip(e.merged_file or e.output_dir)
                if c == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

    def _current(self) -> Optional[HistoryEntry]:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def _open_folder(self, row: int) -> None:
        if not (0 <= row < len(self.rows)):
            return
        e = self.rows[row]
        target = Path(e.merged_file) if e.merged_file and Path(e.merged_file).exists() else Path(e.output_dir)
        if not target.exists():
            QMessageBox.information(self, tr("History"), tr("Files of this generation were deleted or moved."))
            return
        reveal_in_folder(target)

    def _play(self) -> None:
        e = self._current()
        if e and e.merged_file and Path(e.merged_file).exists():
            try:
                player.play(Path(e.merged_file))
            except PlayerError:
                open_path(Path(e.merged_file))

    def _repeat(self) -> None:
        e = self._current()
        if e:
            self.repeat_requested.emit(e)

    def _export(self) -> None:
        path, flt = QFileDialog.getSaveFileName(self, tr("Export history"), "history.csv", "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                Path(path).write_text(json.dumps([e.model_dump(mode="json") for e in self.rows],
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["date", "title", "voice", "model", "chars", "credits", "parts", "state", "file"])
                    for e in self.rows:
                        w.writerow([e.created_at, e.title, e.voice_name, e.model_id, e.chars, e.credits, e.parts,
                                    e.state, e.merged_file or e.output_dir])
            QMessageBox.information(self, tr("Export"), tr("Records saved: {n}", n=len(self.rows)))
        except OSError as ex:
            QMessageBox.warning(self, tr("Export"), tr("Failed to save: {error}", error=ex))
