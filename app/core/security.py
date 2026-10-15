from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from app.core.storage import atomic_write_bytes, atomic_write_json, read_json
from app.utils.paths import data_file

SERVICE = "ElevenManager"
PBKDF2_ITERATIONS = 390_000


def derive_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def fernet_from_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(derive_key(password, salt, iterations)))


class MasterPassword:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_file("security.json")
        self._data: dict = read_json(self.path, {})

    def is_set(self) -> bool:
        return bool(self._data.get("verifier"))

    @property
    def salt(self) -> bytes:
        return base64.b64decode(self._data["salt"])

    @property
    def iterations(self) -> int:
        return int(self._data.get("iterations", PBKDF2_ITERATIONS))

    def _verifier(self, password: str) -> str:
        key = derive_key(password, self.salt, self.iterations)
        return hmac.new(key, b"eleven-manager-verify", hashlib.sha256).hexdigest()

    def create(self, password: str) -> None:
        self._data = {
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "iterations": PBKDF2_ITERATIONS,
        }
        self._data["verifier"] = self._verifier(password)
        atomic_write_json(self.path, self._data)
        logger.info("Master password set")

    def verify(self, password: str) -> bool:
        if not self.is_set():
            return False
        return hmac.compare_digest(self._verifier(password), self._data["verifier"])


# get_keyring() can report a backend that doesn't actually work, so just try it
def _keyring_available() -> bool:
    try:
        import keyring
        from keyring.errors import KeyringError

        backend = keyring.get_keyring()
        name = type(backend).__name__.lower()
        if "fail" in name or "null" in name:
            return False
        probe_user = "__probe__"
        keyring.set_password(SERVICE, probe_user, "1")
        ok = keyring.get_password(SERVICE, probe_user) == "1"
        try:
            keyring.delete_password(SERVICE, probe_user)
        except KeyringError:
            pass
        return ok
    except Exception as e:
        logger.warning("keyring unavailable: {}", e)
        return False


class SecretStore:
    def __init__(self, master: MasterPassword, enc_path: Path | None = None) -> None:
        self.master = master
        self.enc_path = enc_path or data_file("accounts.enc")
        self.backend = "keyring" if _keyring_available() else "fernet"
        self._fernet: Optional[Fernet] = None
        self._cache: dict[str, str] = {}
        logger.info("Key storage: {}", self.backend)

    @property
    def unlocked(self) -> bool:
        return self.backend == "keyring" or self._fernet is not None

    def unlock(self, password: str) -> bool:
        if not self.master.verify(password):
            return False
        if self.backend == "fernet":
            self._fernet = fernet_from_password(password, self.master.salt, self.master.iterations)
            self._cache = self._read_enc()
        return True

    def _read_enc(self) -> dict[str, str]:
        if not self.enc_path.exists() or self._fernet is None:
            return {}
        try:
            raw = self._fernet.decrypt(self.enc_path.read_bytes())
            data = json.loads(raw.decode("utf-8"))
            return {str(k): str(v) for k, v in data.items()}
        except (InvalidToken, ValueError, OSError) as e:
            logger.error("Failed to decrypt accounts.enc: {}", e)
            return {}

    def _write_enc(self) -> None:
        if self._fernet is None:
            raise RuntimeError("Key storage is locked")
        token = self._fernet.encrypt(json.dumps(self._cache).encode("utf-8"))
        atomic_write_bytes(self.enc_path, token)

    def get(self, account_id: str) -> Optional[str]:
        if self.backend == "keyring":
            try:
                import keyring

                return keyring.get_password(SERVICE, account_id)
            except Exception as e:
                logger.error("keyring.get_password: {}", e)
                return None
        return self._cache.get(account_id)

    def set(self, account_id: str, api_key: str) -> None:
        if self.backend == "keyring":
            import keyring

            keyring.set_password(SERVICE, account_id, api_key)
            return
        self._cache[account_id] = api_key
        self._write_enc()

    def delete(self, account_id: str) -> None:
        if self.backend == "keyring":
            try:
                import keyring

                keyring.delete_password(SERVICE, account_id)
            except Exception as e:
                logger.warning("keyring.delete_password: {}", e)
            return
        self._cache.pop(account_id, None)
        self._write_enc()
