import json

from app.core.models import ChunkStatus, GenerationSettings
from app.core.queue import QueueStore, create_task
from app.core.storage import atomic_write_json, read_json
from app.core.text_splitter import split_text


def test_atomic_write_roundtrip(tmp_path):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"a": 1, "текст": "да"})
    assert read_json(path, None) == {"a": 1, "текст": "да"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_corrupted_json_is_moved_aside(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_json(path, "default") == "default"
    assert not path.exists()
    assert len(list(tmp_path.glob("broken.json.corrupt-*"))) == 1


def test_queue_resets_part_that_was_generating(tmp_path):
    chunks = split_text("Раз. Два. Три. " * 50, 200)
    task = create_task("text", chunks, GenerationSettings(voice_id="v"), tmp_path, 1.0, 1)
    task.chunks[0].status = ChunkStatus.GENERATING
    store = QueueStore(tmp_path / "queue.json")
    store.save(task)

    loaded = store.load()
    assert loaded.chunks[0].status == ChunkStatus.PENDING


def test_queue_regenerates_part_with_missing_file(tmp_path):
    chunks = split_text("Раз. Два. Три. " * 50, 200)
    task = create_task("text", chunks, GenerationSettings(voice_id="v"), tmp_path, 1.0, 1)
    task.chunks[0].status = ChunkStatus.DONE
    task.chunks[0].file = str(tmp_path / "gone.mp3")
    store = QueueStore(tmp_path / "queue.json")
    store.save(task)

    assert store.load().chunks[0].status == ChunkStatus.PENDING
    assert json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))["id"] == task.id
