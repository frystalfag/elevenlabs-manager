from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.account import AccountStore
from app.core.cache import ChunkCache
from app.core.history import Favorites, HistoryStore, PresetStore, UsageStore
from app.core.models import AppSettings
from app.core.queue import QueueStore
from app.core.security import MasterPassword, SecretStore
from app.core.storage import atomic_write_json, load_model, read_json, save_model
from app.utils.paths import data_file, default_output_dir


class AppContext:
    def __init__(self) -> None:
        self.settings_path = data_file("settings.json")
        self.settings: AppSettings = load_model(self.settings_path, AppSettings, AppSettings)
        self.master = MasterPassword()
        self.secrets = SecretStore(self.master)
        self.accounts: Optional[AccountStore] = None
        self.queue = QueueStore()
        self.history = HistoryStore()
        self.usage = UsageStore()
        self.presets = PresetStore()
        self.favorites = Favorites()
        self.cache = ChunkCache(enabled=self.settings.cache_enabled)

    def init_accounts(self) -> AccountStore:
        self.accounts = AccountStore(self.secrets)
        return self.accounts

    def save_settings(self) -> None:
        self.cache.enabled = self.settings.cache_enabled
        save_model(self.settings_path, self.settings)

    @property
    def output_root(self) -> Path:
        path = Path(self.settings.output_dir) if self.settings.output_dir else default_output_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def load_cached(name: str) -> list[dict]:
        raw = read_json(data_file(name), [])
        return raw if isinstance(raw, list) else []

    @staticmethod
    def save_cached(name: str, items: list[dict]) -> None:
        atomic_write_json(data_file(name), items)

    def model_info(self, model_id: str) -> Optional[dict]:
        return next((m for m in self.load_cached("models_cache.json") if m.get("model_id") == model_id), None)

    @staticmethod
    def load_draft() -> str:
        path = data_file("draft.txt")
        try:
            return path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            return ""

    @staticmethod
    def save_draft(text: str) -> None:
        from app.core.storage import atomic_write_bytes

        atomic_write_bytes(data_file("draft.txt"), text.encode("utf-8"))
