from __future__ import annotations

import warnings
from pathlib import Path

from loguru import logger

from app.i18n import tr
from app.utils.helpers import find_ffmpeg


class MergeError(Exception):
    pass


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def _configure_pydub():
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise MergeError(tr("ffmpeg not found, can't merge"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        from pydub import AudioSegment
    AudioSegment.converter = str(ffmpeg)
    probe = ffmpeg.with_name(ffmpeg.name.replace("ffmpeg", "ffprobe"))
    if probe.is_file():
        AudioSegment.ffprobe = str(probe)  # type: ignore[attr-defined]
    return AudioSegment


def bitrate_from_format(output_format: str) -> str:
    try:
        return f"{int(output_format.split('_')[-1])}k"
    except (ValueError, IndexError):
        return "128k"


def merge(parts: list[Path], out_path: Path, gap_ms: int = 250, bitrate: str = "128k") -> Path:
    if not parts:
        raise MergeError(tr("Nothing to merge"))
    missing = [p for p in parts if not p.is_file()]
    if missing:
        raise MergeError(tr("Files not found: {files}", files=', '.join(p.name for p in missing[:5])))
    AudioSegment = _configure_pydub()
    try:
        silence = AudioSegment.silent(duration=gap_ms) if gap_ms > 0 else None
        result = AudioSegment.empty()
        for i, part in enumerate(parts):
            if i and silence is not None:
                result += silence
            result += AudioSegment.from_file(part, format="mp3")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp.mp3")
        result.export(tmp, format="mp3", bitrate=bitrate)
        tmp.replace(out_path)
        logger.info("Merged {} parts -> {} ({:.1f} s)", len(parts), out_path.name, len(result) / 1000)
        return out_path
    except MergeError:
        raise
    except Exception as e:
        raise MergeError(tr("Merge failed: {error}", error=e)) from e


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path
