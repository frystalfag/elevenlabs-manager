from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSpinBox, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.context import AppContext
from app.core.models import GenerationSettings, VoiceSettings

# mp3 only: merging raw pcm would need wav headers
OUTPUT_FORMATS = [
    (tr("MP3 44.1 kHz, 128 kbps (recommended)"), "mp3_44100_128"),
    (tr("MP3 44.1 kHz, 192 kbps (Creator+)"), "mp3_44100_192"),
    (tr("MP3 44.1 kHz, 96 kbps"), "mp3_44100_96"),
    (tr("MP3 44.1 kHz, 64 kbps"), "mp3_44100_64"),
    (tr("MP3 22.05 kHz, 32 kbps (saves space)"), "mp3_22050_32"),
]

COMMON_LANGUAGES = [(tr("Auto-detect"), ""), ("Русский", "ru"), ("English", "en"), ("Українська", "uk"),
                    ("Deutsch", "de"), ("Español", "es"), ("Français", "fr"), ("Italiano", "it"),
                    ("Polski", "pl"), ("Português", "pt"), ("Türkçe", "tr"), ("日本語", "ja"), ("中文", "zh")]


class LabeledSlider(QWidget):
    changed = pyqtSignal()

    def __init__(self, lo: int, hi: int, scale: float = 100.0, suffix: str = "") -> None:
        super().__init__()
        self.scale = scale
        self.suffix = suffix
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.label = QLabel()
        self.label.setMinimumWidth(48)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._on_change)

    def _on_change(self, _v: int) -> None:
        self.label.setText(f"{self.value():.2f}{self.suffix}")
        self.changed.emit()

    def value(self) -> float:
        return self.slider.value() / self.scale

    def set_value(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)
        self.label.setText(f"{v:.2f}{self.suffix}")


class SettingsPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, ctx: AppContext, languages_for: Callable[[str], list[tuple[str, str]]],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.languages_for = languages_for
        lay = QVBoxLayout(self)

        voice_box = QGroupBox(tr("Voice"))
        form = QFormLayout(voice_box)
        self.stability = LabeledSlider(0, 100)
        self.similarity = LabeledSlider(0, 100)
        self.style = LabeledSlider(0, 100)
        self.speed = LabeledSlider(70, 120, suffix="×")
        self.boost = QCheckBox(tr("Speaker boost (more similarity, slightly slower)"))
        form.addRow(tr("Stability:"), self.stability)
        form.addRow(tr("Similarity:"), self.similarity)
        form.addRow(tr("Style:"), self.style)
        form.addRow(tr("Speed:"), self.speed)
        form.addRow("", self.boost)
        hint = QLabel(tr("Lower stability is more emotional but less predictable. For long texts we recommend "
                         "0.45-0.6 and style = 0, so all parts sound the same."))
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow(hint)
        lay.addWidget(voice_box)

        out_box = QGroupBox(tr("Output"))
        oform = QFormLayout(out_box)
        self.fmt = QComboBox()
        for label, value in OUTPUT_FORMATS:
            self.fmt.addItem(label, value)
        self.lang = QComboBox()
        self.seed_on = QCheckBox(tr("Fixed seed"))
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        seed_row = QHBoxLayout()
        seed_row.addWidget(self.seed_on)
        seed_row.addWidget(self.seed, 1)
        self.context = QCheckBox(tr("Smooth joins: send neighbouring text (previous/next_text)"))
        oform.addRow(tr("Format:"), self.fmt)
        oform.addRow(tr("Language:"), self.lang)
        oform.addRow("Seed:", seed_row)
        oform.addRow("", self.context)
        lay.addWidget(out_box)

        reset = QPushButton(tr("Reset to recommended"))
        reset.clicked.connect(self._reset)
        lay.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)

        for s in (self.stability, self.similarity, self.style, self.speed):
            s.changed.connect(self._store)
        self.boost.toggled.connect(self._store)
        self.fmt.currentIndexChanged.connect(self._store)
        self.lang.currentIndexChanged.connect(self._store)
        self.seed_on.toggled.connect(self._store)
        self.seed.valueChanged.connect(self._store)
        self.context.toggled.connect(self._store)
        self.load(self.ctx.settings.generation)

    def refresh_languages(self) -> None:
        g = self.ctx.settings.generation
        langs = self.languages_for(g.model_id)
        self.lang.blockSignals(True)
        self.lang.clear()
        self.lang.addItem(tr("Auto-detect"), "")
        for code, name in (langs or COMMON_LANGUAGES[1:]):
            self.lang.addItem(f"{name} ({code})", code)
        idx = self.lang.findData(g.language_code)
        self.lang.setCurrentIndex(max(0, idx))
        self.lang.blockSignals(False)

    def load(self, g: GenerationSettings) -> None:
        vs = g.voice_settings
        self._loading = True
        self.stability.set_value(vs.stability)
        self.similarity.set_value(vs.similarity_boost)
        self.style.set_value(vs.style)
        self.speed.set_value(vs.speed)
        for w, v in ((self.boost, vs.use_speaker_boost), (self.seed_on, g.seed is not None),
                     (self.context, g.use_context)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self.seed.blockSignals(True)
        self.seed.setValue(g.seed or 0)
        self.seed.setEnabled(g.seed is not None)
        self.seed.blockSignals(False)
        self.fmt.blockSignals(True)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(g.output_format)))
        self.fmt.blockSignals(False)
        self.refresh_languages()
        self._loading = False

    def _store(self, *_args: object) -> None:
        if getattr(self, "_loading", False):
            return
        g = self.ctx.settings.generation
        g.voice_settings = VoiceSettings(
            stability=self.stability.value(),
            similarity_boost=self.similarity.value(),
            style=self.style.value(),
            speed=self.speed.value(),
            use_speaker_boost=self.boost.isChecked(),
        )
        g.output_format = self.fmt.currentData() or "mp3_44100_128"
        g.language_code = self.lang.currentData() or ""
        self.seed.setEnabled(self.seed_on.isChecked())
        g.seed = self.seed.value() if self.seed_on.isChecked() else None
        g.use_context = self.context.isChecked()
        self.ctx.save_settings()
        self.changed.emit()

    def _reset(self) -> None:
        g = self.ctx.settings.generation
        g.voice_settings = VoiceSettings()
        g.output_format = "mp3_44100_128"
        g.seed = None
        g.use_context = True
        self.ctx.save_settings()
        self.load(g)
        self.changed.emit()
