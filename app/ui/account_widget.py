from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.account import Account, AccountStatus
from app.core.context import AppContext
from app.core.eleven_client import ElevenClient, PermissionDenied, Unauthorized
from app.ui.dialogs import AddKeyDialog
from app.ui.theme import color
from app.ui.workers import error_text, run_async
from app.utils.helpers import fmt_int, fmt_reset


class AccountCard(QWidget):
    def __init__(self, acc: Account, active: bool) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        dot = "●" if active else "○"
        dot_color = color("ok") if active else color("muted")
        if acc.status == AccountStatus.INVALID:
            dot_color = color("error")
        name = QLabel(f"<span style='color:{dot_color}'>{dot}</span> <b>{acc.name}</b>"
                      f"<span style='color:{color('muted')}'>  {acc.masked_key}</span>")
        lay.addWidget(name)
        if acc.remaining is not None:
            text = tr("Left: <b>{left}</b> / {limit}", left=fmt_int(acc.remaining), limit=fmt_int(acc.character_limit))
        else:
            text = tr("Left: —")
        rem = QLabel(text)
        lay.addWidget(rem)
        meta = QLabel(tr("Reset: {reset} · {tier} · {status}",
                         reset=fmt_reset(acc.next_reset_unix), tier=acc.tier or "—", status=acc.status.label))
        meta.setObjectName("Muted")
        meta.setStyleSheet(f"color:{color('error') if acc.status == AccountStatus.INVALID else color('muted')}")
        lay.addWidget(meta)
        for w in (name, rem, meta):
            w.setWordWrap(True)
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            w.setStyleSheet(w.styleSheet() + "background: transparent;")


class AccountPanel(QWidget):
    active_changed = pyqtSignal(str)
    accounts_updated = pyqtSignal()

    def __init__(self, ctx: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Panel")
        self.setMinimumWidth(250)
        lay = QVBoxLayout(self)
        title = QLabel(tr("Keys"))
        title.setObjectName("Title")
        lay.addWidget(title)
        self.total = QLabel("")
        lay.addWidget(self.total)
        hint = QLabel(tr("Estimates use the active key. Switching is manual only (click)."))
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.list = QListWidget()
        self.list.setSpacing(2)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemClicked.connect(self._clicked)
        lay.addWidget(self.list, 1)

        add = QPushButton(tr("+ Add key"))
        add.setObjectName("Primary")
        add.clicked.connect(self.add_key)
        lay.addWidget(add)
        row = QHBoxLayout()
        self.refresh_btn = QPushButton(tr("Refresh quota"))
        self.refresh_btn.clicked.connect(lambda: self.refresh_all())
        rm = QPushButton(tr("Remove"))
        rm.setObjectName("Danger")
        rm.clicked.connect(self._remove_selected)
        row.addWidget(self.refresh_btn)
        row.addWidget(rm)
        lay.addLayout(row)
        self.locked_while_running = False
        self.render()

    @property
    def store(self):
        assert self.ctx.accounts is not None
        return self.ctx.accounts

    def render(self) -> None:
        self.list.clear()
        for acc in self.store.accounts:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, acc.id)
            card = AccountCard(acc, acc.id == self.store.active_id)
            item.setSizeHint(card.sizeHint())
            if not acc.selectable:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(tr("The key is invalid and can't be selected. Use 'Refresh quota' to recheck."))
            self.list.addItem(item)
            self.list.setItemWidget(item, card)
            if acc.id == self.store.active_id:
                self.list.setCurrentItem(item)
        total = self.store.total_remaining()
        self.total.setText(tr("Total left: <b>{total}</b>", total=fmt_int(total)))
        if not self.store.accounts:
            self.total.setText(tr("No keys yet, add the first one."))

    def _selected_id(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _clicked(self, item: QListWidgetItem) -> None:
        acc_id = item.data(Qt.ItemDataRole.UserRole)
        acc = self.store.get(acc_id)
        if acc is None or acc_id == self.store.active_id:
            return
        if not acc.selectable:
            QMessageBox.information(self, tr("Key"),
                                    tr("This key is marked as invalid. Click 'Refresh quota' to recheck it."))
            self.render()
            return
        if self.store.set_active(acc_id):
            self.render()
            self.active_changed.emit(acc_id)

    def add_key(self) -> None:
        dlg = AddKeyDialog(self)
        if not dlg.exec():
            return
        name, key = dlg.values()
        try:
            acc = self.store.add(name, key)
        except Exception as e:
            QMessageBox.critical(self, tr("Key"), tr("Failed to save the key: {error}", error=e))
            return
        self.render()
        self.refresh_one(acc.id, announce=True)
        self.active_changed.emit(self.store.active_id or "")

    def _remove_selected(self) -> None:
        acc_id = self._selected_id()
        acc = self.store.get(acc_id)
        if not acc:
            return
        msg = tr("Remove key '{name}' ({masked})?\nThe key itself is not revoked in ElevenLabs.",
                 name=acc.name, masked=acc.masked_key)
        if QMessageBox.question(self, tr("Remove key"), msg) != QMessageBox.StandardButton.Yes:
            return
        self.store.remove(acc.id)
        self.render()
        self.active_changed.emit(self.store.active_id or "")

    def refresh_all(self) -> None:
        for acc in self.store.accounts:
            self.refresh_one(acc.id)

    def refresh_one(self, account_id: str, announce: bool = False) -> None:
        key = self.store.api_key(account_id)
        if not key:
            return

        def fetch() -> dict:
            client = ElevenClient(key, retries=1)
            try:
                return client.get_subscription()
            finally:
                client.close()

        def ok(sub: dict) -> None:
            acc = self.store.get(account_id)
            if acc:
                acc.apply_subscription(sub)
                self.store.save()
                self.render()
                self.accounts_updated.emit()

        def err(e: Exception) -> None:
            acc = self.store.get(account_id)
            if acc is None:
                return
            if isinstance(e, Unauthorized):
                self.store.mark_invalid(account_id)
                QMessageBox.warning(self, tr("Key"),
                                    tr("Key '{name}' is invalid: {error}", name=acc.name, error=error_text(e)))
            elif isinstance(e, PermissionDenied):
                acc.status = AccountStatus.NO_PERMISSION
                self.store.save()
                if announce:
                    QMessageBox.information(self, tr("Key"), tr(
                        "The key is saved, but it has no 'User → Read' permission, so the quota "
                        "can't be shown. Generation will still work."))
            elif announce:
                QMessageBox.warning(self, tr("Key"), tr("Couldn't check the key: {error}", error=error_text(e)))
            self.render()
            self.accounts_updated.emit()

        run_async(self, fetch, ok, err)

    def apply_subscription(self, account_id: str, sub: dict) -> None:
        acc = self.store.get(account_id)
        if acc:
            acc.apply_subscription(sub)
            self.store.save()
            self.render()

    def _menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        acc = self.store.get(item.data(Qt.ItemDataRole.UserRole))
        if not acc:
            return
        menu = QMenu(self)
        menu.addAction(tr("Make active"), lambda: self._clicked(item))
        menu.addAction(tr("Rename"), lambda: self._rename(acc))
        menu.addAction(tr("Copy masked key"), lambda: QApplication.clipboard().setText(acc.masked_key))
        menu.addAction(tr("Check / refresh quota"), lambda: self.refresh_one(acc.id, announce=True))
        menu.addAction(tr("Reset local usage counter"), lambda: self._reset_usage(acc))
        menu.addSeparator()
        menu.addAction(tr("Remove"), self._remove_selected)
        self.list.setCurrentItem(item)
        menu.exec(self.list.mapToGlobal(pos))

    def _rename(self, acc: Account) -> None:
        name, ok = QInputDialog.getText(self, tr("Rename"), tr("New name:"), text=acc.name)
        if ok and name.strip():
            acc.name = name.strip()
            self.store.save()
            self.render()
            self.accounts_updated.emit()

    def _reset_usage(self, acc: Account) -> None:
        acc.local_used = 0
        self.store.save()
        self.ctx.usage.reset_account(acc.id)
        self.render()
