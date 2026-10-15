from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.models import Task
from app.utils.helpers import ffmpeg_instructions, fmt_int


class PasswordDialog(QDialog):
    def __init__(self, create: bool, verify: Optional[Callable[[str], bool]] = None,
                 parent: Optional[QWidget] = None, allow_quit: bool = True) -> None:
        super().__init__(parent)
        self.create = create
        self.verify = verify
        self.setWindowTitle(tr("Master password") if create else tr("Unlock"))
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        intro = QLabel(
            tr("Create a master password. It protects your API keys (when the system keyring isn't "
               "available) and unlocks the app. It can't be recovered.")
            if create else tr("Enter the master password.")
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr("Password:"), self.pw)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        if create:
            form.addRow(tr("Repeat:"), self.pw2)
        lay.addLayout(form)
        self.err = QLabel("")
        self.err.setObjectName("Error")
        lay.addWidget(self.err)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        if allow_quit:
            buttons.addButton(tr("Quit"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.password = ""

    def _accept(self) -> None:
        pw = self.pw.text()
        if self.create:
            if len(pw) < 4:
                self.err.setText(tr("At least 4 characters."))
                return
            if pw != self.pw2.text():
                self.err.setText(tr("Passwords don't match."))
                return
        elif self.verify and not self.verify(pw):
            self.err.setText(tr("Wrong password."))
            self.pw.selectAll()
            return
        self.password = pw
        self.accept()


class AddKeyDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Add API key"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        hint = QLabel(
            tr("Create a key in ElevenLabs: Profile → API Keys → Create API Key.\n"
               "To show the remaining quota the key needs the 'User → Read' permission.")
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("e.g. Work / Personal"))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk_...")
        form.addRow(tr("Name:"), self.name)
        form.addRow(tr("API key:"), self.key)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept(self) -> None:
        if len(self.key.text().strip()) < 20:
            QMessageBox.warning(self, tr("Key"), tr("This doesn't look like an ElevenLabs API key."))
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.name.text().strip() or tr("Key"), self.key.text().strip()


def show_ffmpeg_dialog(parent: Optional[QWidget]) -> None:
    box = QDialog(parent)
    box.setWindowTitle(tr("ffmpeg required"))
    box.setMinimumSize(560, 320)
    lay = QVBoxLayout(box)
    title = QLabel(tr("Generation is disabled: ffmpeg not found"))
    title.setObjectName("Title")
    lay.addWidget(title)
    text = QPlainTextEdit(ffmpeg_instructions())
    text.setReadOnly(True)
    lay.addWidget(text)
    btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    btn.accepted.connect(box.accept)
    lay.addWidget(btn)
    box.exec()


class RestoreTaskDialog(QDialog):
    RESUME, CANCEL, LATER = 1, 2, 3

    def __init__(self, task: Task, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Unfinished task"))
        self.choice = self.LATER
        lay = QVBoxLayout(self)
        msg = QLabel(
            tr("Found an unfinished task '{title}':\n"
               "{done}/{total} parts done, state: {state}.\n"
               "About {chars} characters left.\n\nContinue?",
               title=task.title, done=task.done_count, total=task.total, state=task.state.label,
               chars=fmt_int(sum(c.chars for c in task.pending_chunks)))
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)
        row = QHBoxLayout()
        for text, value, primary in ((tr("Continue"), self.RESUME, True), (tr("Cancel task"), self.CANCEL, False),
                                     (tr("Later"), self.LATER, False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("Primary")
            b.clicked.connect(lambda _=False, v=value: self._choose(v))
            row.addWidget(b)
        lay.addLayout(row)

    def _choose(self, value: int) -> None:
        self.choice = value
        self.accept()


class LockScreen(QWidget):
    unlocked = pyqtSignal()

    def __init__(self, verify: Callable[[str], bool], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.verify = verify
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        box = QWidget()
        box.setObjectName("Panel")
        box.setFixedWidth(360)
        lay = QVBoxLayout(box)
        title = QLabel(tr("🔒 App is locked"))
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText(tr("Master password"))
        self.pw.returnPressed.connect(self._try)
        lay.addWidget(self.pw)
        self.err = QLabel("")
        self.err.setObjectName("Error")
        lay.addWidget(self.err)
        btn = QPushButton(tr("Unlock"))
        btn.setObjectName("Primary")
        btn.clicked.connect(self._try)
        lay.addWidget(btn)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(box)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)

    def reset(self) -> None:
        self.pw.clear()
        self.err.clear()
        self.pw.setFocus()

    def _try(self) -> None:
        if self.verify(self.pw.text()):
            self.pw.clear()
            self.err.clear()
            self.unlocked.emit()
        else:
            self.err.setText(tr("Wrong password"))
            self.pw.selectAll()


def ask_password(parent: Optional[QWidget], title: str, label: str) -> Optional[str]:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(label))
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    lay.addWidget(edit)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    if dlg.exec() == QDialog.DialogCode.Accepted and edit.text():
        return edit.text()
    return None
