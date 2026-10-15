from __future__ import annotations

from typing import Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.models import Chunk, ChunkStatus
from app.core.planner import Plan, plan_summary
from app.ui.theme import color
from app.utils.helpers import fmt_int

STATUS_COLORS = {
    ChunkStatus.PENDING: "muted",
    ChunkStatus.GENERATING: "warn",
    ChunkStatus.DONE: "ok",
    ChunkStatus.CACHED: "ok",
    ChunkStatus.FAILED: "error",
}


class ChunkTable(QTableWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, 4, parent)
        self.setHorizontalHeaderLabels(["#", tr("Chars"), tr("Preview"), tr("Status")])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

    def set_chunks(self, chunks: list[Chunk], limit_index: Optional[int] = None) -> None:
        self.setRowCount(len(chunks))
        for row, ch in enumerate(chunks):
            preview = ch.text[:140].replace("\n", " ") + ("…" if len(ch.text) > 140 else "")
            self.setItem(row, 0, QTableWidgetItem(str(ch.index)))
            self.setItem(row, 1, QTableWidgetItem(fmt_int(ch.chars)))
            item = QTableWidgetItem(preview)
            item.setToolTip(ch.text[:1500])
            self.setItem(row, 2, item)
            self.set_status(ch.index, ch.status, ch.error)
            if limit_index is not None and ch.index > limit_index:
                for col in range(3):
                    cell = self.item(row, col)
                    if cell:
                        cell.setForeground(QColor(color("muted")))

    def set_status(self, index: int, status: ChunkStatus | str, error: Optional[str] = None) -> None:
        status = ChunkStatus(status)
        row = index - 1
        if row < 0 or row >= self.rowCount():
            return
        item = QTableWidgetItem(status.label)
        item.setForeground(QColor(color(STATUS_COLORS[status])))
        if error:
            item.setToolTip(error)
        self.setItem(row, 3, item)
        if status == ChunkStatus.GENERATING:
            self.scrollToItem(item)


class PlanDialog(QDialog):
    def __init__(self, plan: Plan, voice: str, model: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Generation plan"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        head = QLabel(tr("Voice: <b>{voice}</b> · Model: <b>{model}</b>", voice=voice, model=model))
        lay.addWidget(head)
        summary = QLabel(plan_summary(plan))
        summary.setWordWrap(True)
        summary.setObjectName("Panel")
        summary.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(summary)
        if not plan.fits_fully and plan.account_remaining is not None:
            warn = QLabel(tr("Parts after #{n} are greyed out - there's not enough quota for them right now.",
                             n=plan.parts_now))
            warn.setObjectName("Warn")
            warn.setWordWrap(True)
            lay.addWidget(warn)

        self.table = ChunkTable()
        self.table.set_chunks(plan.chunks, None if plan.fits_fully else plan.parts_now)
        self.table.setMinimumHeight(280)
        self.table.hide()
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.toggle = QPushButton(tr("Show plan"))
        self.toggle.clicked.connect(self._toggle)
        start = QPushButton(tr("Generate"))
        start.setObjectName("Primary")
        start.clicked.connect(self.accept)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        row.addWidget(self.toggle)
        row.addStretch(1)
        row.addWidget(start)
        row.addWidget(cancel)
        lay.addLayout(row)
        if plan.parts_now == 0 and plan.account_remaining is not None:
            lim = QLabel(tr("The active key doesn't have enough quota even for the first part. You can still "
                            "create the task - it will pause right away and continue after the reset or a key switch."))
            lim.setObjectName("Error")
            lim.setWordWrap(True)
            lay.addWidget(lim)

    def _toggle(self) -> None:
        visible = not self.table.isVisible()
        self.table.setVisible(visible)
        self.toggle.setText(tr("Hide plan") if visible else tr("Show plan"))
        self.adjustSize()
