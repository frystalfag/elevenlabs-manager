from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from app.i18n import tr
from app.utils.helpers import open_path

STATUS_COLORS = {
    "idle": "#4cc38a",
    "running": "#f0b542",
    "paused": "#5b8cff",
    "error": "#ef6461",
}


def status_icon(status: str, size: int = 64) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#1e2128"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, size - 4, size - 4, 14, 14)
    p.setBrush(QColor(STATUS_COLORS.get(status, STATUS_COLORS["idle"])))
    bars = [0.35, 0.65, 0.95, 0.65, 0.35]
    bw = size / 11
    for i, k in enumerate(bars):
        bh = (size - 20) * k
        x = size / 2 - (len(bars) * bw * 1.6) / 2 + i * bw * 1.6
        p.drawRoundedRect(int(x), int((size - bh) / 2), int(bw), int(bh), 3, 3)
    p.end()
    return QIcon(pix)


class TrayManager(QObject):
    show_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(status_icon("idle"), parent)
        self.tray.setToolTip("ElevenLabs Manager")
        self._last_file: Optional[Path] = None
        menu = QMenu()
        self.menu = menu
        self.act_show = QAction(tr("Show"), menu, triggered=self.show_requested.emit)
        self.act_pause = QAction(tr("Pause"), menu, triggered=self.pause_requested.emit)
        self.act_resume = QAction(tr("Continue"), menu, triggered=self.resume_requested.emit)
        self.act_quit = QAction(tr("Quit"), menu, triggered=self.quit_requested.emit)
        for a in (self.act_show, self.act_pause, self.act_resume):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.messageClicked.connect(self._message_clicked)
        if self.available:
            self.tray.show()
        self.set_status("idle")

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_requested.emit()

    # tray notifications can't have buttons, so clicking opens the file
    def _message_clicked(self) -> None:
        if self._last_file and self._last_file.exists():
            open_path(self._last_file)
        else:
            self.show_requested.emit()

    def set_status(self, status: str, tooltip: str = "") -> None:
        self.tray.setIcon(status_icon(status))
        self.tray.setToolTip(f"ElevenLabs Manager — {tooltip}" if tooltip else "ElevenLabs Manager")
        self.act_pause.setEnabled(status == "running")
        self.act_resume.setEnabled(status in ("paused", "error"))

    def notify(self, title: str, message: str, file: Optional[Path] = None, error: bool = False) -> None:
        self._last_file = file
        if not self.available:
            return
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, message, icon, 8000)

    def hide(self) -> None:
        self.tray.hide()
