from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from app.i18n import tr
from app.core.profile import ProfileError, export_profile, import_profile
from app.core.updater import check_for_update
from app.ui.dialogs import ask_password
from app.ui.workers import run_async
from app.utils.paths import APP_VERSION, data_dir


def restart_app() -> None:
    args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    QProcess.startDetached(sys.executable, args)


class MenuActionsMixin:
    def _set_language(self, lang: str) -> None:
        if lang == self.ctx.settings.language:
            return
        self.ctx.settings.language = lang
        self.ctx.save_settings()
        if self.controller.running:
            QMessageBox.information(self, tr("Language"), tr("The language will change on the next launch."))
            return
        answer = QMessageBox.question(self, tr("Language"), tr("Restart the app now to switch the language?"))
        if answer == QMessageBox.StandardButton.Yes:
            self._restart_after_quit = True
            self.quit_app()

    def _export_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Export profile"), "eleven_profile.zip", "ZIP (*.zip)")
        if not path:
            return
        pw = ask_password(self, tr("Archive password"), tr("Password to encrypt the keys in the archive:"))
        if not pw:
            return
        try:
            export_profile(Path(path), self.ctx.accounts, pw)
            QMessageBox.information(self, tr("Export"), tr("Profile saved. Keys in the archive are encrypted."))
        except ProfileError as e:
            QMessageBox.warning(self, tr("Export"), str(e))

    def _import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import profile"), "", "ZIP (*.zip)")
        if not path:
            return
        pw = ask_password(self, tr("Archive password"), tr("Password used during export:"))
        if not pw:
            return
        try:
            n = import_profile(Path(path), self.ctx.accounts, pw)
        except ProfileError as e:
            QMessageBox.warning(self, tr("Import"), str(e))
            return
        QMessageBox.information(self, tr("Import"),
                                tr("Keys imported: {n}.\nRestart the app to apply settings and history.", n=n))
        self.accounts.render()

    def _about(self) -> None:
        text = tr("ElevenLabs Manager {version}\n\n"
                  "Resumable voiceover for long texts via the ElevenLabs API.\n"
                  "Key storage: {backend}\nData: {data}",
                  version=APP_VERSION, backend=self.ctx.secrets.backend, data=data_dir())
        QMessageBox.about(self, tr("About"), text)

    def check_updates(self, manual: bool = False) -> None:
        repo = self.ctx.settings.update_repo
        if not repo:
            if manual:
                QMessageBox.information(self, tr("Updates"), tr("Set the repository in Preferences."))
            return

        def ok(info) -> None:
            if info:
                msg = tr("Version {version} is available:\n{url}", version=info.version, url=info.url)
                QMessageBox.information(self, tr("Update"), msg)
            elif manual:
                QMessageBox.information(self, tr("Updates"), tr("You're on the latest version."))

        run_async(self, lambda: check_for_update(repo), ok)
