from __future__ import annotations

import sys
from pathlib import Path

# allow `python app/main.py` as well as `python -m app.main`
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.i18n import set_language, tr
from app.utils.paths import APP_NAME, APP_VERSION, data_file, ensure_dirs
from app.utils.logger import setup_logger


def _excepthook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
    logger.opt(exception=(exc_type, exc, tb)).critical("Unhandled exception")
    if QApplication.instance():
        msg = tr("Unexpected error:\n{error}\n\nSee the logs for details.", error=exc)
        QMessageBox.critical(None, tr("Error"), msg)


def unlock_or_create(ctx) -> bool:  # type: ignore[no-untyped-def]
    from app.ui.dialogs import PasswordDialog

    if not ctx.master.is_set():
        dlg = PasswordDialog(create=True)
        if not dlg.exec():
            return False
        ctx.master.create(dlg.password)
        return ctx.secrets.unlock(dlg.password)
    dlg = PasswordDialog(create=False, verify=ctx.master.verify)
    if not dlg.exec():
        return False
    return ctx.secrets.unlock(dlg.password)


def main() -> int:
    ensure_dirs()
    setup_logger(debug="--debug" in sys.argv)
    logger.info("Starting {} {} (Python {})", APP_NAME, APP_VERSION, sys.version.split()[0])
    sys.excepthook = _excepthook

    # has to happen before the ui modules are imported, some of them build labels at import time
    from app.core.models import AppSettings
    from app.core.storage import load_model
    set_language(load_model(data_file("settings.json"), AppSettings, AppSettings).language)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    # the window may be hidden in the tray while generating
    app.setQuitOnLastWindowClosed(False)

    from app.core.audio_merger import ffmpeg_available
    from app.core.context import AppContext
    from app.core.storage import daily_backup
    from app.ui.dialogs import show_ffmpeg_dialog
    from app.ui.main_window import MainWindow
    from app.ui.theme import apply_theme

    try:
        ctx = AppContext()
    except Exception as e:
        logger.exception("Failed to load app data")
        QMessageBox.critical(None, tr("Startup error"), tr("Failed to load app data:\n{error}", error=e))
        return 1
    apply_theme(app, ctx.settings.theme)

    if not unlock_or_create(ctx):
        logger.info("Startup cancelled at the password step")
        return 0
    ctx.init_accounts()
    ctx.settings.last_backup_date = daily_backup(ctx.settings.last_backup_date)
    ctx.save_settings()

    window = MainWindow(ctx)
    window.show()

    def after_show() -> None:
        if not ffmpeg_available():
            logger.warning("ffmpeg not found, generation disabled")
            show_ffmpeg_dialog(window)
        window.offer_restore()
        if ctx.settings.check_updates:
            window.check_updates()
        if ctx.accounts and ctx.accounts.accounts:
            window.accounts.refresh_all()

    QTimer.singleShot(300, after_show)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
