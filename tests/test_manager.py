"""Generation loop against a fake ElevenLabs client: pause on quota, resume with another key, retry failed."""
import pytest

from app.core import manager
from app.core.account import Account
from app.core.cache import ChunkCache
from app.core.eleven_client import NetworkError, QuotaExceeded, TransientError, TTSResult
from app.core.models import AppSettings, ChunkStatus, GenerationSettings, TaskState
from app.core.planner import build_plan
from app.core.queue import QueueStore, create_task, reset_failed

TEXT = "\n\n".join(f"Абзац номер {i}. " + "Текст предложения. " * 20 for i in range(1, 9))


class FakeClient:
    quota = {}  # key -> how many parts it can still voice
    fail_texts = set()
    offline = False
    calls = []

    def __init__(self, key, **kw):
        self.key = key

    def close(self):
        pass

    def get_subscription(self):
        if FakeClient.offline:
            raise NetworkError("offline")
        return {"character_count": 0, "character_limit": 10**9}

    def get_voice(self, voice_id):
        return {"voice_id": voice_id}

    def tts(self, text, *args, **kwargs):
        FakeClient.calls.append((self.key, text))
        if FakeClient.quota.get(self.key, 0) <= 0:
            raise QuotaExceeded("quota", 401, "quota_exceeded")
        if text in FakeClient.fail_texts:
            raise TransientError("500")
        FakeClient.quota[self.key] -= 1
        return TTSResult(audio=b"ID3" + text.encode()[:20], character_cost=len(text), request_id="x")


@pytest.fixture
def setup(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "ElevenClient", FakeClient)
    FakeClient.quota = {"A": 3, "B": 100}
    FakeClient.fail_texts = set()
    FakeClient.offline = False
    FakeClient.calls = []
    settings = GenerationSettings(voice_id="v1", voice_name="Voice")
    plan = build_plan(TEXT, settings, None, chunk_size=450)
    queue = QueueStore(tmp_path / "queue.json")
    task = create_task(TEXT, plan.chunks, settings, tmp_path / "out", 1.0, 1)
    queue.save(task)
    return task, queue


def run(task, queue, key, retry_failed=False):
    events = []
    th = manager.GenerationTask(task, queue, key, Account(id=key, name=key, masked_key="x"),
                                ChunkCache(enabled=False), AppSettings(auto_merge=False), retry_failed)
    th.paused.connect(lambda reason: events.append(("paused", reason)))
    th.task_finished.connect(lambda path: events.append(("finished", path)))
    th.failed.connect(lambda msg: events.append(("failed", msg)))
    th.run()  # synchronously, no need for a real thread here
    return events


def test_pauses_on_quota_and_saves_progress(setup):
    task, queue = setup
    events = run(task, queue, "A")
    saved = queue.load()
    assert events == [("paused", "limit")]
    assert saved.state == TaskState.PAUSED_LIMIT
    assert saved.done_count == 3
    assert saved.chunks[3].status == ChunkStatus.PENDING


def test_resume_with_another_key_starts_from_next_part(setup):
    task, queue = setup
    run(task, queue, "A")
    FakeClient.calls.clear()

    restored = queue.load()  # as if the app was restarted
    events = run(restored, queue, "B")

    assert FakeClient.calls[0] == ("B", restored.chunks[3].text)
    assert events[-1][0] == "finished"
    assert queue.load().state == TaskState.DONE


def test_failed_part_is_skipped_then_retried(setup):
    task, queue = setup
    FakeClient.quota["A"] = 100
    FakeClient.fail_texts = {task.chunks[5].text}

    events = run(task, queue, "A")
    assert events[-1][0] == "failed"
    assert task.state == TaskState.PARTIAL
    assert task.failed_count == 1

    FakeClient.fail_texts = set()
    FakeClient.calls.clear()
    reset_failed(task)
    run(task, queue, "A")
    assert len(FakeClient.calls) == 1
    assert task.state == TaskState.DONE


def test_network_error_pauses(setup):
    task, queue = setup
    FakeClient.offline = True
    assert run(task, queue, "B") == [("paused", "network")]
    assert queue.load().state == TaskState.PAUSED_NETWORK
