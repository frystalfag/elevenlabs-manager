from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.context import AppContext
from app.utils.helpers import render_template


class PreferencesDialog(QDialog):
    def __init__(self, ctx: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        s = ctx.settings
        self.setWindowTitle(tr("Preferences"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.theme = QComboBox()
        for label, value in ((tr("Follow system"), "auto"), (tr("Dark"), "dark"), (tr("Light"), "light")):
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        form.addRow(tr("Theme:"), self.theme)

        out_row = QHBoxLayout()
        self.output = QLineEdit(s.output_dir)
        self.output.setPlaceholderText(str(ctx.output_root))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        out_row.addWidget(self.output, 1)
        out_row.addWidget(browse)
        form.addRow(tr("Output folder:"), out_row)

        self.template = QLineEdit(s.filename_template)
        self.template.textChanged.connect(self._preview)
        self.template_preview = QLabel("")
        self.template_preview.setObjectName("Muted")
        form.addRow(tr("File name template:"), self.template)
        form.addRow("", self.template_preview)

        self.chunk = QSpinBox()
        self.chunk.setRange(200, 40000)
        self.chunk.setSingleStep(500)
        self.chunk.setValue(s.chunk_size)
        self.chunk.setSuffix(tr(" chars"))
        self.chunk.setToolTip(tr("Size of one part. Capped by the model limit."))
        form.addRow(tr("Part size:"), self.chunk)

        self.gap = QSpinBox()
        self.gap.setRange(0, 5000)
        self.gap.setSingleStep(50)
        self.gap.setValue(s.merge_gap_ms)
        self.gap.setSuffix(tr(" ms"))
        form.addRow(tr("Pause between parts:"), self.gap)

        self.auto_merge = QCheckBox(tr("Merge parts into one file after generation"))
        self.auto_merge.setChecked(s.auto_merge)
        self.delete_parts = QCheckBox(tr("Delete parts after a successful merge"))
        self.delete_parts.setChecked(s.delete_parts_after_merge)
        self.cache = QCheckBox(tr("Cache parts (same text + settings costs no credits)"))
        self.cache.setChecked(s.cache_enabled)
        self.tray = QCheckBox(tr("Minimize to tray on close while generating"))
        self.tray.setChecked(s.minimize_to_tray)
        for cb in (self.auto_merge, self.delete_parts, self.cache, self.tray):
            form.addRow("", cb)

        cache_row = QHBoxLayout()
        size_mb = ctx.cache.size_bytes() / 1024 / 1024
        cache_row.addWidget(QLabel(tr("Cache size: {mb:.1f} MB", mb=size_mb)))
        clear = QPushButton(tr("Clear cache"))
        clear.clicked.connect(self._clear_cache)
        cache_row.addWidget(clear)
        cache_row.addStretch(1)
        form.addRow("", cache_row)

        self.lock = QSpinBox()
        self.lock.setRange(0, 600)
        self.lock.setValue(s.auto_lock_minutes)
        self.lock.setSuffix(tr(" min"))
        self.lock.setSpecialValueText(tr("off"))
        form.addRow(tr("Auto-lock:"), self.lock)

        self.updates = QCheckBox(tr("Check for updates on startup"))
        self.updates.setChecked(s.check_updates)
        self.repo = QLineEdit(s.update_repo)
        self.repo.setPlaceholderText(tr("owner/repo on GitHub"))
        form.addRow("", self.updates)
        form.addRow(tr("Repository:"), self.repo)

        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._preview()

    def _browse(self) -> None:
        start = self.output.text() or str(self.ctx.output_root)
        path = QFileDialog.getExistingDirectory(self, tr("Output folder"), start)
        if path:
            self.output.setText(path)

    def _preview(self) -> None:
        name = render_template(self.template.text() or "{date}", voice="Rachel", model="eleven_multilingual_v2",
                               index=12, title=tr("Chapter one"))
        self.template_preview.setText(tr(
            "Example: {name}.mp3   ·  available: {{date}} {{time}} {{voice}} {{model}} {{index}} {{title}}",
            name=name))

    def _clear_cache(self) -> None:
        if QMessageBox.question(self, tr("Cache"), tr("Delete all cached parts?")) == QMessageBox.StandardButton.Yes:
            self.ctx.cache.clear()

    def _save(self) -> None:
        s = self.ctx.settings
        s.theme = self.theme.currentData()
        s.output_dir = self.output.text().strip()
        s.filename_template = self.template.text().strip() or "{date}_{voice}_{index}"
        s.chunk_size = self.chunk.value()
        s.merge_gap_ms = self.gap.value()
        s.auto_merge = self.auto_merge.isChecked()
        s.delete_parts_after_merge = self.delete_parts.isChecked()
        s.cache_enabled = self.cache.isChecked()
        s.minimize_to_tray = self.tray.isChecked()
        s.auto_lock_minutes = self.lock.value()
        s.check_updates = self.updates.isChecked()
        s.update_repo = self.repo.text().strip()
        self.ctx.save_settings()
        self.accept()
