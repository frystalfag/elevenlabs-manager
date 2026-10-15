from __future__ import annotations

from typing import Any, Callable, Optional

from loguru import logger
from PyQt6.QtCore import QObject, QThread, pyqtSignal


class Worker(QThread):
    ok = pyqtSignal(object)
    err = pyqtSignal(object)

    def __init__(self, fn: Callable[[], Any], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        try:
            self.ok.emit(self.fn())
        except Exception as e:
            logger.debug("Background job failed: {!r}", e)
            self.err.emit(e)


def run_async(
    owner: QObject,
    fn: Callable[[], Any],
    on_ok: Optional[Callable[[Any], None]] = None,
    on_err: Optional[Callable[[Exception], None]] = None,
) -> Worker:
    worker = Worker(fn, owner)
    # keep a ref, otherwise GC can kill the QThread while it runs
    jobs: list[Worker] = owner.__dict__.setdefault("_bg_jobs", [])
    jobs.append(worker)
    if on_ok:
        worker.ok.connect(on_ok)
    if on_err:
        worker.err.connect(on_err)

    def cleanup() -> None:
        if worker in jobs:
            jobs.remove(worker)
        worker.deleteLater()

    worker.finished.connect(cleanup)
    worker.start()
    return worker


def error_text(e: Exception) -> str:
    return getattr(e, "message", None) or str(e) or e.__class__.__name__
