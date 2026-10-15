from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from app.utils.paths import resource_path

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#16181d", "panel": "#1e2128", "input": "#14161a", "button": "#262a33",
        "hover": "#2f3440", "border": "#343a46", "fg": "#e6e8ee", "muted": "#8a92a3",
        "accent": "#6c8cff", "accent_hover": "#829dff", "accent_fg": "#ffffff",
        "selection": "#2b3550", "ok": "#4cc38a", "warn": "#f0b542", "error": "#ef6461",
    },
    "light": {
        "bg": "#f4f5f8", "panel": "#ffffff", "input": "#ffffff", "button": "#eef0f4",
        "hover": "#e2e6ee", "border": "#d3d8e2", "fg": "#1d2129", "muted": "#677084",
        "accent": "#3d63f0", "accent_hover": "#5476f2", "accent_fg": "#ffffff",
        "selection": "#dbe3ff", "ok": "#1f9d61", "warn": "#b7790f", "error": "#d64541",
    },
}

current_palette: dict[str, str] = PALETTES["dark"]


def system_is_dark() -> bool:
    app = QApplication.instance()
    try:
        scheme = app.styleHints().colorScheme()  # type: ignore[union-attr]
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:
        pass
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128  # type: ignore[union-attr]


def resolve(mode: str) -> str:
    if mode == "auto":
        return "dark" if system_is_dark() else "light"
    return mode if mode in PALETTES else "dark"


def apply_theme(app: QApplication, mode: str) -> str:
    global current_palette
    name = resolve(mode)
    pal = PALETTES[name]
    current_palette = pal
    try:
        qss = resource_path("app/ui/styles.qss").read_text(encoding="utf-8")
    except OSError as e:
        logger.error("styles.qss not found: {}", e)
        qss = ""
    # longest first so @accent doesn't eat @accent_fg
    for key in sorted(pal, key=len, reverse=True):
        qss = qss.replace(f"@{key}", pal[key])
    app.setStyle("Fusion")
    qpal = QPalette()
    qpal.setColor(QPalette.ColorRole.Window, QColor(pal["bg"]))
    qpal.setColor(QPalette.ColorRole.WindowText, QColor(pal["fg"]))
    qpal.setColor(QPalette.ColorRole.Base, QColor(pal["input"]))
    qpal.setColor(QPalette.ColorRole.Text, QColor(pal["fg"]))
    qpal.setColor(QPalette.ColorRole.Button, QColor(pal["button"]))
    qpal.setColor(QPalette.ColorRole.ButtonText, QColor(pal["fg"]))
    qpal.setColor(QPalette.ColorRole.Highlight, QColor(pal["accent"]))
    qpal.setColor(QPalette.ColorRole.HighlightedText, QColor(pal["accent_fg"]))
    qpal.setColor(QPalette.ColorRole.PlaceholderText, QColor(pal["muted"]))
    app.setPalette(qpal)
    app.setStyleSheet(qss)
    return name


def color(name: str) -> str:
    return current_palette.get(name, "#888888")
