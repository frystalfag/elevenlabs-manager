from app.core.account import Account
from app.core.models import GenerationSettings
from app.core.planner import build_plan, count_fitting, effective_chunk_size

TEXT = "\n\n".join(f"Абзац {i}. " + "Текст предложения. " * 20 for i in range(12))


def account(limit, used=0):
    return Account(name="test", masked_key="sk_test****", character_limit=limit, character_count=used)


def test_fits_fully():
    plan = build_plan(TEXT, GenerationSettings(voice_id="v"), account(100_000), chunk_size=500)
    assert plan.fits_fully
    assert plan.parts_now == plan.parts_total


def test_partial_fit():
    plan = build_plan(TEXT, GenerationSettings(voice_id="v"), account(1000, used=200), chunk_size=400)
    assert not plan.fits_fully
    assert 0 < plan.parts_now < plan.parts_total
    spent = sum(c.chars for c in plan.chunks[:plan.parts_now])
    assert spent <= 800


def test_unknown_quota_assumes_everything_fits():
    plan = build_plan(TEXT, GenerationSettings(voice_id="v"), None, chunk_size=500)
    assert plan.account_remaining is None
    assert plan.parts_now == plan.parts_total


def test_cached_parts_are_free():
    plan = build_plan(TEXT, GenerationSettings(voice_id="v"), account(10), chunk_size=500)
    cached = {c.hash for c in plan.chunks}
    assert count_fitting(plan.chunks, 10, 1.0, cached) == len(plan.chunks)


def test_chunk_size_capped_by_model():
    assert effective_chunk_size(40_000, "eleven_v3", None) == 5000
    assert effective_chunk_size(2500, "eleven_flash_v2_5", None) == 2500
    info = {"maximum_text_length_per_request": 1200}
    assert effective_chunk_size(2500, "whatever", info) == 1200
