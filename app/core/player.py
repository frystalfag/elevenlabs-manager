from __future__ import annotations

import os
from pathlib import Path

from loguru import logger
from app.i18n import tr

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


class PlayerError(Exception):
    pass


class AudioPlayer:
    def __init__(self) -> None:
        self._ready = False
        self.current: Path | None = None

    def _ensure(self) -> None:
        if self._ready:
            return
        try:
            import pygame

            pygame.mixer.init()
            self._ready = True
        except Exception as e:
            raise PlayerError(tr("Audio unavailable: {error}", error=e)) from e

    def play(self, path: Path) -> None:
        self._ensure()
        import pygame

        try:
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
            self.current = path
        except Exception as e:
            raise PlayerError(tr("Can't play {name}: {error}", name=path.name, error=e)) from e

    def stop(self) -> None:
        if not self._ready:
            return
        import pygame

        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception as e:
            logger.debug("stop: {}", e)
        self.current = None

    def is_playing(self) -> bool:
        if not self._ready:
            return False
        import pygame

        return bool(pygame.mixer.music.get_busy())


player = AudioPlayer()
