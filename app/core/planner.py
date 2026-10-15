from __future__ import annotations

import math
from typing import Optional

from pydantic import BaseModel, Field

from app.i18n import tr
from app.core.account import Account
from app.core.models import Chunk, ChunkStatus, GenerationSettings
from app.core.text_splitter import split_text
from app.utils.helpers import fmt_duration, fmt_int

DEFAULT_MODEL_LIMITS: dict[str, int] = {
    "eleven_v3": 5000,
    "eleven_multilingual_v2": 10000,
    "eleven_flash_v2_5": 40000,
    "eleven_turbo_v2_5": 40000,
    "eleven_flash_v2": 30000,
    "eleven_turbo_v2": 30000,
    "eleven_monolingual_v1": 10000,
    "eleven_multilingual_v1": 10000,
}

DEFAULT_SEC_PER_KCHAR = 3.0
REQUEST_OVERHEAD_SEC = 1.5


class Plan(BaseModel):
    chunks: list[Chunk] = Field(default_factory=list)
    total_chars: int = 0
    account_remaining: Optional[int] = None
    fits_fully: bool = True
    parts_now: int = 0
    parts_total: int = 0
    cached_parts: int = 0
    estimated_seconds: float = 0.0
    estimated_credits: int = 0
    cost_multiplier: float = 1.0
    max_chunk_chars: int = 0


def model_limit(model_id: str, model_info: Optional[dict]) -> int:
    if model_info and model_info.get("maximum_text_length_per_request"):
        return int(model_info["maximum_text_length_per_request"])
    return DEFAULT_MODEL_LIMITS.get(model_id, 5000)


def cost_multiplier(model_info: Optional[dict]) -> float:
    if not model_info:
        return 1.0
    rates = model_info.get("model_rates") or {}
    try:
        return float(rates.get("character_cost_multiplier", 1.0)) or 1.0
    except (TypeError, ValueError):
        return 1.0


def chunk_credits(chars: int, multiplier: float) -> int:
    return int(math.ceil(chars * multiplier))


# smaller chunks sound more consistent and waste less quota when we pause on the limit
def effective_chunk_size(user_chunk_size: int, model_id: str, model_info: Optional[dict]) -> int:
    return max(200, min(user_chunk_size, model_limit(model_id, model_info)))


def count_fitting(chunks: list[Chunk], remaining: Optional[int], multiplier: float,
                  cached_hashes: set[str] | None = None) -> int:
    if remaining is None:
        return len(chunks)
    cached_hashes = cached_hashes or set()
    budget = remaining
    for i, ch in enumerate(chunks):
        if ch.is_complete or ch.hash in cached_hashes:
            continue
        cost = chunk_credits(ch.chars, multiplier)
        if cost > budget:
            return i
        budget -= cost
    return len(chunks)


def estimate_seconds(chunks: list[Chunk], sec_per_kchar: Optional[float]) -> float:
    rate = sec_per_kchar or DEFAULT_SEC_PER_KCHAR
    pending = [c for c in chunks if c.status != ChunkStatus.DONE and c.status != ChunkStatus.CACHED]
    return sum(REQUEST_OVERHEAD_SEC + c.chars / 1000 * rate for c in pending)


def build_plan(
    text: str,
    settings: GenerationSettings,
    account: Optional[Account],
    *,
    chunk_size: int = 2500,
    model_info: Optional[dict] = None,
    sec_per_kchar: Optional[float] = None,
    cached_hashes: set[str] | None = None,
) -> Plan:
    size = effective_chunk_size(chunk_size, settings.model_id, model_info)
    chunks = split_text(text, size, settings.fingerprint())
    mult = cost_multiplier(model_info)
    cached_hashes = cached_hashes or set()
    cached = sum(1 for c in chunks if c.hash in cached_hashes)
    credits = sum(chunk_credits(c.chars, mult) for c in chunks if c.hash not in cached_hashes)
    remaining = account.remaining if account else None
    parts_now = count_fitting(chunks, remaining, mult, cached_hashes)
    return Plan(
        chunks=chunks,
        total_chars=sum(c.chars for c in chunks),
        account_remaining=remaining,
        fits_fully=parts_now >= len(chunks),
        parts_now=parts_now,
        parts_total=len(chunks),
        cached_parts=cached,
        estimated_seconds=estimate_seconds(chunks, sec_per_kchar),
        estimated_credits=credits,
        cost_multiplier=mult,
        max_chunk_chars=size,
    )


def plan_summary(plan: Plan) -> str:
    lines = [tr("Text: {chars} characters, {parts} parts.", chars=fmt_int(plan.total_chars), parts=plan.parts_total)]
    if plan.cost_multiplier != 1.0:
        lines.append(tr("Model cost: ×{mult:g} → ~{credits} credits.",
                        mult=plan.cost_multiplier, credits=fmt_int(plan.estimated_credits)))
    else:
        lines.append(tr("Credits needed: ~{credits}.", credits=fmt_int(plan.estimated_credits)))
    if plan.cached_parts:
        lines.append(tr("From cache (free): {n} parts.", n=plan.cached_parts))
    if plan.account_remaining is None:
        lines.append(tr("Remaining quota unknown, refresh it in the left panel."))
    elif plan.fits_fully:
        lines.append(tr("Quota is enough (left on key: {left}).", left=fmt_int(plan.account_remaining)))
    else:
        lines.append(
            tr("Enough for {now} of {total} parts (left on key: {left}).",
               now=plan.parts_now, total=plan.parts_total, left=fmt_int(plan.account_remaining))
        )
    lines.append(tr("Estimated time: ~{time}.", time=fmt_duration(plan.estimated_seconds)))
    if not plan.fits_fully and plan.account_remaining is not None:
        lines.append(
            tr("The task will pause after part {n} - continue once the quota resets or after switching keys.",
               n=plan.parts_now)
        )
    return "\n".join(lines)
