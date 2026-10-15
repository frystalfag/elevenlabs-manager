from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from loguru import logger
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox, QSplitter, QStackedWidget, QTabWidget,
)

from app.i18n import LANGUAGES, language, tr
from app.core.context import AppContext
from app.core.models import HistoryEntry
from app.ui.account_widget import AccountPanel
from app.ui.dialogs import LockScreen, RestoreTaskDialog
from app.ui.history_view import HistoryView
from app.ui.logs_view import LogsView
from app.ui.menu_actions import MenuActionsMixin, restart_app
from app.ui.model_picker import ModelPicker
from app.ui.preferences_dialog import PreferencesDialog
from app.ui.queue_view import QueueView
from app.ui.settings_panel import SettingsPanel
from app.ui.stats_view import StatsView
from app.ui.task_controller import TaskController
from app.ui.text_panel import TextPanel, read_text_file
from app.ui.theme import apply_theme
from app.ui.tray import TrayManager, status_icon
from app.ui.voice_picker import VoicePicker
from app.utils.helpers import fmt_int, open_path
from app.utils.paths import APP_VERSION, data_dir

TAB_VOICE, TAB_MODEL, TAB_PARAMS, TAB_TEXT, TAB_QUEUE = range(5)


class MainWindow(MenuActionsMixin, QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._quitting = False
        self._last_activity = time.monotonic()
        self.setWindowTitle(f"ElevenLabs Manager {APP_VERSION}")
        self.setWindowIcon(status_icon("idle"))
        self.resize(1400, 860)
        self.setAcceptDrops(True)

        self.controller = TaskController(ctx, self)
        self.accounts = AccountPanel(ctx)
        get_key = self._active_key
        self.voices = VoicePicker(ctx, get_key)
        self.models = ModelPicker(ctx, get_key)
        self.params = SettingsPanel(ctx, self.models.languages)
        self.text = TextPanel(ctx, lambda: ctx.usage.sec_per_kchar)
        self.queue = QueueView()
        self.history = HistoryView(ctx)
        self.stats = StatsView(ctx)
        self.logs = LogsView()
        self.tray = TrayManager(self)

        self.center = QTabWidget()
        for widget, title in ((self.voices, tr("Voice")), (self.models, tr("Model")), (self.params, tr("Settings")),
                              (self.text, tr("Text")), (self.queue, tr("Queue"))):
            self.center.addTab(widget, title)
        self.right = QTabWidget()
        self.right.addTab(self.history, tr("History"))
        self.right.addTab(self.stats, tr("Insights"))
        self.right.addTab(self.logs, tr("Logs"))
        self.right.currentChanged.connect(lambda i: self.stats.render() if i == 1 else None)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.accounts)
        splitter.addWidget(self.center)
        splitter.addWidget(self.right)
        splitter.setSizes([280, 760, 360])
        splitter.setChildrenCollapsible(False)
        self.lock_screen = LockScreen(ctx.master.verify)
        self.lock_screen.unlocked.connect(self.unlock)
        self.stack = QStackedWidget()
        self.stack.addWidget(splitter)
        self.stack.addWidget(self.lock_screen)
        self.setCentralWidget(self.stack)

        self._build_status_bar()
        self._build_menu()
        self._build_shortcuts()
        self._wire()
        self.queue.set_task(self.controller.task)
        self.queue.set_active_account(ctx.accounts.active_id if ctx.accounts else None)
        self.center.setCurrentIndex(TAB_TEXT)
        self.text.update_estimate()
        self.update_status_bar()

        QApplication.instance().installEventFilter(self)
        self._lock_timer = QTimer(self, interval=15_000)
        self._lock_timer.timeout.connect(self._check_idle)
        self._lock_timer.start()

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.sb_key = QLabel()
        self.sb_remaining = QLabel()
        self.sb_model = QLabel()
        self.sb_voice = QLabel()
        self.sb_task = QLabel()
        for w in (self.sb_key, self.sb_remaining, self.sb_model, self.sb_voice, self.sb_task):
            sb.addPermanentWidget(w)

    def _build_menu(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu(tr("File"))
        file_menu.addAction(tr("Open .txt…"), self._open_text)
        file_menu.addSeparator()
        file_menu.addAction(tr("Export profile…"), self._export_profile)
        file_menu.addAction(tr("Import profile…"), self._import_profile)
        file_menu.addSeparator()
        file_menu.addAction(tr("Preferences…"), self._preferences)
        lock = QAction(tr("Lock"), self)
        lock.setShortcut(QKeySequence("Ctrl+L"))
        lock.triggered.connect(self.lock)
        file_menu.addAction(lock)
        file_menu.addSeparator()
        file_menu.addAction(tr("Quit"), self.quit_app)
        view = mb.addMenu(tr("View"))
        for label, mode in ((tr("Dark theme"), "dark"), (tr("Light theme"), "light"), (tr("Follow system"), "auto")):
            view.addAction(label, lambda m=mode: self._set_theme(m))
        lang_menu = view.addMenu(tr("Language"))
        for code, name in LANGUAGES.items():
            act = lang_menu.addAction(name, lambda c=code: self._set_language(c))
            act.setCheckable(True)
            act.setChecked(code == language())
        help_menu = mb.addMenu(tr("Help"))
        help_menu.addAction(tr("Check for updates"), lambda: self.check_updates(manual=True))
        help_menu.addAction(tr("Open data folder"), lambda: open_path(data_dir()))
        help_menu.addAction(tr("About"), self._about)

    def _build_shortcuts(self) -> None:
        def sc(keys: str, fn) -> None:
            QShortcut(QKeySequence(keys), self, activated=fn)

        sc("Ctrl+Return", self._generate)
        sc("Ctrl+Enter", self._generate)
        sc("Ctrl+P", self._focus_presets)
        sc("Ctrl+H", lambda: self.right.setCurrentWidget(self.history))
        sc("Esc", self.controller.pause)
        for i in range(5):
            sc(f"Ctrl+{i + 1}", lambda i=i: self.center.setCurrentIndex(i))

    def _wire(self) -> None:
        c = self.controller
        self.accounts.active_changed.connect(self._on_active_changed)
        self.accounts.accounts_updated.connect(self._on_accounts_changed)
        self.voices.voice_selected.connect(self._on_voice)
        self.models.model_selected.connect(self._on_model)
        self.params.changed.connect(self.text.update_estimate)
        self.text.generate_requested.connect(self._generate)
        self.text.test_requested.connect(c.test_voice)
        self.text.preset_applied.connect(self._on_preset)
        self.queue.pause_clicked.connect(c.pause)
        self.queue.resume_clicked.connect(lambda: c.resume())
        self.queue.retry_failed_clicked.connect(lambda: c.resume(retry_failed=True))
        self.queue.cancel_clicked.connect(c.cancel)
        self.queue.merge_clicked.connect(c.merge_now)
        self.history.repeat_requested.connect(self._repeat)
        self.logs.error_logged.connect(lambda line: self.statusBar().showMessage(line[:160], 10_000))
        c.task_changed.connect(self._on_task_changed)
        c.chunk_status.connect(self.queue.on_chunk_status)
        c.running_changed.connect(self._on_running)
        c.status_changed.connect(self._on_status)
        c.accounts_changed.connect(self._on_accounts_changed)
        c.notify.connect(self.tray.notify)
        self.tray.show_requested.connect(self.show_normal)
        self.tray.pause_requested.connect(c.pause)
        self.tray.resume_requested.connect(lambda: c.resume())
        self.tray.quit_requested.connect(self.quit_app)

    def _active_key(self) -> Optional[str]:
        acc = self.ctx.accounts.active if self.ctx.accounts else None
        return self.ctx.accounts.api_key(acc.id) if acc else None

    def update_status_bar(self) -> None:
        acc = self.ctx.accounts.active if self.ctx.accounts else None
        g = self.ctx.settings.generation
        self.sb_key.setText(f"🔑 {acc.name} ({acc.masked_key})" if acc else tr("🔑 no key"))
        self.sb_remaining.setText(tr("Left: {left}", left=fmt_int(acc.remaining) if acc else '—'))
        self.sb_model.setText(tr("Model: {model}", model=g.model_name or g.model_id))
        self.sb_voice.setText(tr("Voice: {voice}", voice=g.voice_name or '—'))
        t = self.controller.task
        if t:
            self.sb_task.setText(tr("Task: {state} {done}/{total}",
                                    state=t.state.label, done=t.done_count, total=t.total))
        else:
            self.sb_task.setText(tr("Task: none"))

    def _generate(self) -> None:
        self.text.flush_draft()
        self.controller.start(self.text.text())
        if self.controller.running:
            self.center.setCurrentIndex(TAB_QUEUE)

    def _focus_presets(self) -> None:
        self.center.setCurrentIndex(TAB_TEXT)
        self.text.focus_presets()

    def _on_active_changed(self, account_id: str) -> None:
        self.queue.set_active_account(account_id or None)
        self._on_accounts_changed()

    def _on_accounts_changed(self) -> None:
        self.accounts.render()
        self.text.update_estimate()
        self.update_status_bar()

    def _on_voice(self, voice_id: str, name: str) -> None:
        g = self.ctx.settings.generation
        g.voice_id, g.voice_name = voice_id, name
        self.ctx.save_settings()
        self.text.update_estimate()
        self.update_status_bar()

    def _on_model(self, model_id: str, name: str) -> None:
        g = self.ctx.settings.generation
        g.model_id, g.model_name = model_id, name
        self.ctx.save_settings()
        self.params.refresh_languages()
        self.text.update_estimate()
        self.update_status_bar()

    def _on_preset(self) -> None:
        self.params.load(self.ctx.settings.generation)
        self.models.render()
        self.voices.show_current()
        self.voices.render()
        self.update_status_bar()

    def _on_task_changed(self, task) -> None:
        self.queue.set_task(task)
        self.history.render()
        self.update_status_bar()

    def _on_running(self, running: bool) -> None:
        self.queue.set_running(running)
        self.text.set_busy(running)
        self.update_status_bar()

    def _on_status(self, status: str, text: str) -> None:
        self.tray.set_status(status, text)
        self.setWindowIcon(status_icon(status))
        self.queue.refresh()
        self.update_status_bar()

    def _repeat(self, entry: HistoryEntry) -> None:
        src = Path(entry.source_file) if entry.source_file else None
        if src and src.is_file():
            self.text.set_text(read_text_file(src))
        if entry.settings:
            self.ctx.settings.generation = entry.settings.model_copy(deep=True)
            self.ctx.save_settings()
            self._on_preset()
        self.center.setCurrentIndex(TAB_TEXT)
        self.text.update_estimate()

    def _open_text(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Open text"), "", tr("Text (*.txt)"))
        if path:
            self.text.load_file(Path(path))
            self.center.setCurrentIndex(TAB_TEXT)

    def _preferences(self) -> None:
        if PreferencesDialog(self.ctx, self).exec():
            apply_theme(QApplication.instance(), self.ctx.settings.theme)
            self.accounts.render()
            self.text.update_estimate()

    def _set_theme(self, mode: str) -> None:
        self.ctx.settings.theme = mode  # type: ignore[assignment]
        self.ctx.save_settings()
        apply_theme(QApplication.instance(), mode)
        self.accounts.render()
        self.stats.render()


    def offer_restore(self) -> None:
        t = self.controller.task
        if not t or not t.state.is_resumable or not (t.pending_chunks or t.failed_count):
            return
        dlg = RestoreTaskDialog(t, self)
        dlg.exec()
        self.center.setCurrentIndex(TAB_QUEUE)
        if dlg.choice == RestoreTaskDialog.RESUME:
            self.controller.resume(retry_failed=t.failed_count > 0 and not t.pending_chunks)
        elif dlg.choice == RestoreTaskDialog.CANCEL:
            self.controller.cancel()
        else:
            self.controller.defer()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove):
            self._last_activity = time.monotonic()
        return False

    def _check_idle(self) -> None:
        minutes = self.ctx.settings.auto_lock_minutes
        if minutes and self.stack.currentIndex() == 0 and time.monotonic() - self._last_activity > minutes * 60:
            logger.info("Auto-lock after {} min of inactivity", minutes)
            self.lock()

    def lock(self) -> None:
        self.menuBar().setEnabled(False)
        self.stack.setCurrentIndex(1)
        self.lock_screen.reset()

    def unlock(self) -> None:
        self.stack.setCurrentIndex(0)
        self.menuBar().setEnabled(True)
        self._last_activity = time.monotonic()

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls() and self.stack.currentIndex() == 0:
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        for url in e.mimeData().urls():
            if url.toLocalFile().lower().endswith(".txt"):
                self.text.load_file(Path(url.toLocalFile()))
                self.center.setCurrentIndex(TAB_TEXT)
                break

    def show_normal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e: QCloseEvent) -> None:
        if not self._quitting and self.controller.running and self.ctx.settings.minimize_to_tray \
                and self.tray.available:
            e.ignore()
            self.hide()
            self.tray.notify(tr("Still generating"), tr("The app was minimized to the tray."))
            return
        if not self._quitting and self.controller.running:
            answer = QMessageBox.question(
                self, tr("Quit"), tr("Generation is running. Pause and quit? You can continue on the next launch."))
            if answer != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        self._shutdown()
        e.accept()
        QApplication.instance().quit()

    def quit_app(self) -> None:
        self._quitting = True
        if self.controller.running:
            self.show_normal()
            if QMessageBox.question(self, tr("Quit"), tr("Generation is running. Pause and quit?")) != \
                    QMessageBox.StandardButton.Yes:
                self._quitting = False
                return
        self.close()

    def _shutdown(self) -> None:
        self.text.flush_draft()
        self.controller.shutdown()
        self.logs.detach()
        self.tray.hide()
        logger.info("App closed")
        if getattr(self, "_restart_after_quit", False):
            restart_app()
