from __future__ import annotations

from typing import Optional

from loguru import logger
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from app.i18n import tr
from app.utils.helpers import open_path
from app.utils.logger import add_ui_sink
from app.utils.paths import logs_dir

MAX_LINES = 2000


# loguru calls the sink from any thread, hop over to the ui thread via a signal
class _Bridge(QObject):
    line = pyqtSignal(str, str)


class LogsView(QWidget):
    error_logged = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_LINES)
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        clear = QPushButton(tr("Clear"))
        clear.clicked.connect(self.text.clear)
        folder = QPushButton(tr("Logs folder"))
        folder.clicked.connect(lambda: open_path(logs_dir()))
        row.addWidget(clear)
        row.addWidget(folder)
        row.addStretch(1)
        lay.addLayout(row)
        self._bridge = _Bridge()
        self._bridge.line.connect(self._append)
        self._sink_id = add_ui_sink(lambda level, line: self._bridge.line.emit(level, line))

    def _append(self, level: str, line: str) -> None:
        self.text.appendPlainText(line)
        self.text.moveCursor(QTextCursor.MoveOperation.End)
        if level in ("ERROR", "CRITICAL"):
            self.error_logged.emit(line)

    def detach(self) -> None:
        try:
            logger.remove(self._sink_id)
        except ValueError:
            pass
