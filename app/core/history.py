from __future__ import annotations

import threading
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

from app.core.models import GenerationSettings, HistoryEntry, Preset, Task
from app.core.storage import JsonList, atomic_write_json, read_json
from app.utils.paths import data_file


class HistoryStore(JsonList):
    def __init__(self) -> None:
        super().__init__(data_file("history.json"), HistoryEntry, limit=2000)

    def next_index(self) -> int:
        return len(self.items) + 1

    def add_from_task(self, task: Task) -> HistoryEntry:
        source = f"{task.output_dir}/source.txt" if task.output_dir else None
        entry = HistoryEntry(
            id=task.id,
            title=task.title,
            voice_id=task.settings.voice_id,
            voice_name=task.settings.voice_name,
            model_id=task.settings.model_id,
            chars=task.total_chars,
            credits=task.credits_spent,
            parts=task.total,
            state=task.state.value,
            output_dir=task.output_dir,
            merged_file=task.merged_file,
            source_file=source,
            seconds=task.elapsed_seconds,
            settings=task.settings,
        )
        self.items = [e for e in self.items if e.id != task.id]
        self.items.append(entry)
        self.save()
        return entry

    def remove(self, entry_id: str) -> None:
        self.items = [e for e in self.items if e.id != entry_id]
        self.save()


class UsageStore:
    def __init__(self) -> None:
        self.path = data_file("usage.json")
        self._lock = threading.Lock()
        raw = read_json(self.path, {})
        self.days: dict[str, dict[str, float]] = raw.get("days", {}) if isinstance(raw, dict) else {}
        self.by_account: dict[str, int] = raw.get("by_account", {}) if isinstance(raw, dict) else {}
        self.sec_per_kchar: Optional[float] = raw.get("sec_per_kchar") if isinstance(raw, dict) else None

    def save(self) -> None:
        atomic_write_json(self.path, {
            "days": self.days, "by_account": self.by_account, "sec_per_kchar": self.sec_per_kchar,
        })

    def record(self, account_id: str, credits: int, chars: int, seconds: float) -> None:
        with self._lock:
            day = self.days.setdefault(date.today().isoformat(), {"credits": 0, "chars": 0, "requests": 0})
            day["credits"] = day.get("credits", 0) + credits
            day["chars"] = day.get("chars", 0) + chars
            day["requests"] = day.get("requests", 0) + 1
            self.by_account[account_id] = self.by_account.get(account_id, 0) + credits
            if chars > 0 and seconds > 0:
                sample = seconds / (chars / 1000)
                # ewma so one slow request doesn't wreck the estimate
                self.sec_per_kchar = sample if self.sec_per_kchar is None else 0.8 * self.sec_per_kchar + 0.2 * sample
            self.save()

    def reset_account(self, account_id: str) -> None:
        with self._lock:
            self.by_account.pop(account_id, None)
            self.save()

    def last_days(self, n: int = 30) -> list[tuple[str, int]]:
        today = date.today()
        out = []
        for i in range(n - 1, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            out.append((d, int(self.days.get(d, {}).get("credits", 0))))
        return out

    def week_total(self, weeks_ago: int = 0) -> int:
        end = date.today() - timedelta(days=7 * weeks_ago)
        return sum(int(self.days.get((end - timedelta(days=i)).isoformat(), {}).get("credits", 0)) for i in range(7))


def top_voices(history: list[HistoryEntry], n: int = 5) -> list[tuple[str, int]]:
    counter = Counter(e.voice_name or e.voice_id for e in history if e.voice_id)
    return counter.most_common(n)


def average_chars(history: list[HistoryEntry]) -> int:
    values = [e.chars for e in history if e.chars]
    return int(sum(values) / len(values)) if values else 0


def entries_since(history: list[HistoryEntry], days: int) -> list[HistoryEntry]:
    border = datetime.now() - timedelta(days=days)
    out = []
    for e in history:
        try:
            if datetime.fromisoformat(e.created_at) >= border:
                out.append(e)
        except ValueError:
            continue
    return out


class PresetStore(JsonList):
    def __init__(self) -> None:
        super().__init__(data_file("presets.json"), Preset)

    def names(self) -> list[str]:
        return [p.name for p in self.items]

    def get(self, name: str) -> Optional[Preset]:
        return next((p for p in self.items if p.name == name), None)

    def upsert(self, name: str, settings: GenerationSettings) -> None:
        self.items = [p for p in self.items if p.name != name]
        self.items.append(Preset(name=name, settings=settings.model_copy(deep=True)))
        self.save()

    def remove(self, name: str) -> None:
        self.items = [p for p in self.items if p.name != name]
        self.save()


class Favorites:
    def __init__(self) -> None:
        self.path = data_file("favorites.json")
        raw = read_json(self.path, [])
        self.ids: set[str] = set(raw) if isinstance(raw, list) else set()

    def toggle(self, voice_id: str) -> bool:
        if voice_id in self.ids:
            self.ids.discard(voice_id)
        else:
            self.ids.add(voice_id)
        atomic_write_json(self.path, sorted(self.ids))
        return voice_id in self.ids
