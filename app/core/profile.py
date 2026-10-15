from __future__ import annotations

import base64
import json
import os
import zipfile
from pathlib import Path

from cryptography.fernet import InvalidToken
from loguru import logger

from app.i18n import tr
from app.core.account import Account, AccountStore
from app.core.security import fernet_from_password
from app.utils.paths import data_file

PROFILE_FILES = ("presets.json", "history.json", "settings.json", "favorites.json", "usage.json")


class ProfileError(Exception):
    pass


def export_profile(target: Path, accounts: AccountStore, password: str) -> None:
    # keys never go into the zip in plain text
    salt = os.urandom(16)
    fernet = fernet_from_password(password, salt)
    secrets = {a.id: accounts.api_key(a.id) or "" for a in accounts.accounts}
    payload = {
        "accounts": [a.model_dump(mode="json") for a in accounts.accounts],
        "active_id": accounts.active_id,
        "keys": fernet.encrypt(json.dumps(secrets).encode()).decode(),
        "salt": base64.b64encode(salt).decode(),
    }
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("accounts.json", json.dumps(payload, ensure_ascii=False, indent=2))
            for name in PROFILE_FILES:
                src = data_file(name)
                if src.exists():
                    zf.write(src, arcname=name)
        logger.info("Profile exported: {}", target)
    except OSError as e:
        raise ProfileError(tr("Failed to write the archive: {error}", error=e)) from e


def import_profile(source: Path, accounts: AccountStore, password: str) -> int:
    try:
        with zipfile.ZipFile(source) as zf:
            names = set(zf.namelist())
            payload = json.loads(zf.read("accounts.json")) if "accounts.json" in names else {}
            secrets: dict[str, str] = {}
            if payload.get("keys"):
                fernet = fernet_from_password(password, base64.b64decode(payload["salt"]))
                try:
                    secrets = json.loads(fernet.decrypt(payload["keys"].encode()))
                except InvalidToken as e:
                    raise ProfileError(tr("Wrong archive password")) from e
            for name in PROFILE_FILES:
                if name in names:
                    data_file(name).write_bytes(zf.read(name))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as e:
        raise ProfileError(tr("The archive is damaged or has a wrong format: {error}", error=e)) from e

    existing = {a.masked_key for a in accounts.accounts}
    imported = 0
    for row in payload.get("accounts", []):
        try:
            acc = Account.model_validate(row)
        except ValueError:
            continue
        key = secrets.get(acc.id)
        if not key or acc.masked_key in existing:
            continue
        new = accounts.add(acc.name, key)
        new.character_count, new.character_limit = acc.character_count, acc.character_limit
        new.next_reset_unix, new.tier, new.status = acc.next_reset_unix, acc.tier, acc.status
        imported += 1
    accounts.save()
    logger.info("Profile imported, keys: {}", imported)
    return imported
