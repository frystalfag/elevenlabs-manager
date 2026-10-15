from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.i18n import tr
from app.core.security import SecretStore
from app.core.storage import atomic_write_json, read_json
from app.utils.helpers import mask_key, now_iso
from app.utils.paths import data_file


class AccountStatus(str, Enum):
    OK = "ok"
    UNKNOWN = "unknown"
    INVALID = "invalid"
    NO_PERMISSION = "no_permission"

    @property
    def label(self) -> str:
        return {
            "ok": tr("active"),
            "unknown": tr("not checked"),
            "invalid": tr("invalid"),
            "no_permission": tr("no access to limits"),
        }[self.value]


class Account(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str
    masked_key: str
    status: AccountStatus = AccountStatus.UNKNOWN
    tier: str = ""
    character_count: int = 0
    character_limit: Optional[int] = None
    next_reset_unix: Optional[int] = None
    can_clone: bool = False
    last_checked: Optional[str] = None
    local_used: int = 0
    created_at: str = Field(default_factory=now_iso)

    @property
    def remaining(self) -> Optional[int]:
        if self.character_limit is None:
            return None
        return max(0, self.character_limit - self.character_count)

    @property
    def selectable(self) -> bool:
        return self.status != AccountStatus.INVALID

    def apply_subscription(self, sub: dict) -> None:
        self.character_count = int(sub.get("character_count") or 0)
        limit = sub.get("character_limit")
        self.character_limit = int(limit) if limit is not None else None
        self.next_reset_unix = sub.get("next_character_count_reset_unix")
        self.tier = str(sub.get("tier") or "")
        self.can_clone = bool(sub.get("can_use_instant_voice_cloning"))
        self.status = AccountStatus.OK
        self.last_checked = now_iso()

    def spend_locally(self, credits: int) -> None:
        self.character_count += credits
        self.local_used += credits


class AccountStore:
    def __init__(self, secrets: SecretStore) -> None:
        self.path = data_file("accounts.json")
        self.secrets = secrets
        self.accounts: list[Account] = []
        self.active_id: Optional[str] = None
        self.load()

    def load(self) -> None:
        raw = read_json(self.path, {})
        self.accounts = []
        for row in raw.get("accounts", []) if isinstance(raw, dict) else []:
            try:
                self.accounts.append(Account.model_validate(row))
            except ValueError:
                logger.warning("Skipped a broken key record")
        self.active_id = raw.get("active_id") if isinstance(raw, dict) else None
        if self.active is None:
            self.active_id = next((a.id for a in self.accounts if a.selectable), None)

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {"active_id": self.active_id, "accounts": [a.model_dump(mode="json") for a in self.accounts]},
        )

    def get(self, account_id: Optional[str]) -> Optional[Account]:
        return next((a for a in self.accounts if a.id == account_id), None)

    @property
    def active(self) -> Optional[Account]:
        acc = self.get(self.active_id)
        return acc if acc and acc.selectable else None

    def api_key(self, account_id: str) -> Optional[str]:
        return self.secrets.get(account_id)

    def add(self, name: str, api_key: str) -> Account:
        api_key = api_key.strip()
        acc = Account(name=name.strip() or tr("Key"), masked_key=mask_key(api_key))
        self.secrets.set(acc.id, api_key)
        self.accounts.append(acc)
        if self.active is None:
            self.active_id = acc.id
        self.save()
        logger.info("Added key {} ({})", acc.name, acc.masked_key)
        return acc

    def remove(self, account_id: str) -> None:
        self.secrets.delete(account_id)
        self.accounts = [a for a in self.accounts if a.id != account_id]
        if self.active_id == account_id:
            self.active_id = next((a.id for a in self.accounts if a.selectable), None)
        self.save()

    def set_active(self, account_id: str) -> bool:
        acc = self.get(account_id)
        if acc is None or not acc.selectable:
            return False
        self.active_id = account_id
        self.save()
        logger.info("Active key: {} ({})", acc.name, acc.masked_key)
        return True

    def mark_invalid(self, account_id: str) -> None:
        acc = self.get(account_id)
        if acc:
            acc.status = AccountStatus.INVALID
            acc.last_checked = now_iso()
            self.save()
            logger.warning("Key {} marked as invalid", acc.masked_key)

    def total_remaining(self) -> Optional[int]:
        values = [a.remaining for a in self.accounts if a.selectable and a.remaining is not None]
        return sum(values) if values else None

    def seconds_to_reset(self, account_id: str) -> Optional[float]:
        acc = self.get(account_id)
        if acc and acc.next_reset_unix:
            return max(0.0, acc.next_reset_unix - time.time())
        return None
