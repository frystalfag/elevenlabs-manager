from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.core.context import AppContext
from app.core.eleven_client import ElevenClient
from app.core.planner import DEFAULT_MODEL_LIMITS, cost_multiplier, model_limit
from app.ui.workers import error_text, run_async
from app.utils.helpers import fmt_int

FALLBACK_MODELS: list[dict] = [
    {"model_id": "eleven_multilingual_v2", "name": "Eleven Multilingual v2",
     "description": tr("Most natural speech in 29 languages. Best choice for long texts and audiobooks.")},
    {"model_id": "eleven_v3", "name": "Eleven v3",
     "description": tr("Most expressive model, 70+ languages, supports audio tags. 5000 characters per request.")},
    {"model_id": "eleven_flash_v2_5", "name": "Eleven Flash v2.5",
     "description": tr("Ultra fast and cheaper in credits, 32 languages. "
                       "Slightly lower quality than Multilingual v2.")},
    {"model_id": "eleven_turbo_v2_5", "name": "Eleven Turbo v2.5",
     "description": tr("Balance of quality and speed, 32 languages.")},
]


class ModelPicker(QWidget):
    model_selected = pyqtSignal(str, str)

    def __init__(self, ctx: AppContext, get_key: Callable[[], Optional[str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.get_key = get_key
        self.models: list[dict] = ctx.load_cached("models_cache.json") or FALLBACK_MODELS
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QLabel("")
        self.source.setObjectName("Muted")
        top.addWidget(self.source, 1)
        self.refresh_btn = QPushButton(tr("Refresh from server"))
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)
        lay.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        lay.addWidget(self.scroll, 1)
        self.group = QButtonGroup(self)
        self.group.idClicked.connect(self._clicked)
        self.render()

    def render(self) -> None:
        for b in self.group.buttons():
            self.group.removeButton(b)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        current = self.ctx.settings.generation.model_id
        for i, m in enumerate(self.models):
            mid = m.get("model_id", "")
            rb = QRadioButton(m.get("name", mid))
            rb.setStyleSheet("font-weight: 600;")
            rb.setChecked(mid == current)
            self.group.addButton(rb, i)
            lay.addWidget(rb)
            langs = m.get("languages") or []
            lang_text = tr("Languages: {n}", n=len(langs)) if langs else ""
            limit = model_limit(mid, m if "maximum_text_length_per_request" in m else None)
            mult = cost_multiplier(m)
            details = [
                f"id: {mid}",
                tr("up to {limit} chars/request", limit=fmt_int(limit)),
                tr("cost ×{mult:g}", mult=mult),
            ]
            if lang_text:
                details.append(lang_text)
            desc = QLabel(f"{m.get('description', '')}\n{' · '.join(details)}")
            desc.setWordWrap(True)
            desc.setObjectName("Muted")
            desc.setContentsMargins(26, 0, 0, 10)
            lay.addWidget(desc)
        lay.addStretch(1)
        self.scroll.setWidget(holder)
        cached = self.models is not FALLBACK_MODELS
        self.source.setText(tr("List from the ElevenLabs server") if cached else
                            tr("Built-in list. Click 'Refresh' to see the models on your plan."))

    def _clicked(self, idx: int) -> None:
        m = self.models[idx]
        self.model_selected.emit(m.get("model_id", ""), m.get("name", ""))

    def available_ids(self) -> set[str]:
        return {m.get("model_id", "") for m in self.models} | set(DEFAULT_MODEL_LIMITS)

    def refresh(self) -> None:
        key = self.get_key()
        if not key:
            QMessageBox.information(self, tr("Models"), tr("Add and select an active key first."))
            return
        self.refresh_btn.setEnabled(False)

        def fetch() -> list[dict]:
            client = ElevenClient(key)
            try:
                return client.list_models()
            finally:
                client.close()

        def ok(models: list[dict]) -> None:
            self.refresh_btn.setEnabled(True)
            if models:
                self.models = models
                self.ctx.save_cached("models_cache.json", models)
            self.render()

        def err(e: Exception) -> None:
            self.refresh_btn.setEnabled(True)
            QMessageBox.warning(self, tr("Models"), tr("Failed to load models: {error}", error=error_text(e)))

        run_async(self, fetch, ok, err)

    def languages(self, model_id: str) -> list[tuple[str, str]]:
        m = next((x for x in self.models if x.get("model_id") == model_id), None)
        return [(lang.get("language_id", ""), lang.get("name", "")) for lang in (m or {}).get("languages") or []]
