from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from loguru import logger

from app.utils.paths import cache_dir


class ChunkCache:
    def __init__(self, enabled: bool = True, root: Path | None = None) -> None:
        self.enabled = enabled
        self.root = root or cache_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, chunk_hash: str) -> Path:
        return self.root / chunk_hash[:2] / f"{chunk_hash}.mp3"

    def has(self, chunk_hash: str) -> bool:
        return self.enabled and self._path(chunk_hash).is_file()

    def known_hashes(self) -> set[str]:
        if not self.enabled:
            return set()
        return {p.stem for p in self.root.glob("*/*.mp3")}

    def get(self, chunk_hash: str, dest: Path) -> Optional[Path]:
        if not self.has(chunk_hash):
            return None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self._path(chunk_hash), dest)
            return dest
        except OSError as e:
            logger.warning("Cache: failed to copy {}: {}", chunk_hash[:10], e)
            return None

    def put(self, chunk_hash: str, src: Path) -> None:
        if not self.enabled:
            return
        target = self._path(chunk_hash)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
        except OSError as e:
            logger.warning("Cache: failed to store {}: {}", chunk_hash[:10], e)

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.glob("*/*.mp3"))

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        logger.info("Chunk cache cleared")
