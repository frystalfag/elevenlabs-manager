from __future__ import annotations

import re
from typing import Callable

from app.core.models import Chunk, chunk_hash

Span = tuple[int, int]

SAFETY_MARGIN = 0.05
MIN_LIMIT = 50

ABBREVIATIONS: frozenset[str] = frozenset({
    "т.е", "т.д", "т.п", "т.к", "т.н", "т.ч", "др", "пр", "см", "ср", "напр", "г", "гг", "ул",
    "д", "кв", "пл", "пер", "просп", "им", "стр", "рис", "табл", "гл", "тыс", "млн", "млрд",
    "руб", "коп", "долл", "проф", "акад", "доц", "св", "ст", "обл", "р-н", "с", "п", "т", "вв", "в",
    "мин", "сек", "ч", "кг", "км", "см", "мм", "л", "англ", "рус", "лат", "род", "ок", "прим", "ред",
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "inc", "ltd",
    "co", "corp", "no", "fig", "approx", "dept", "est", "jan", "feb", "mar", "apr", "jun", "jul",
    "aug", "sep", "sept", "oct", "nov", "dec", "mt", "ave", "u.s", "u.k",
})
# these often end a sentence ("... и т.д. Потом"), split if the next word is capitalized
LIST_END_ABBR: frozenset[str] = frozenset({"т.д", "т.п", "др", "пр", "etc", "т.ч"})

CONJUNCTIONS = (
    "и", "а", "но", "или", "что", "чтобы", "который", "которая", "которое", "которые", "которого",
    "которой", "которым", "когда", "если", "потому", "поэтому", "хотя", "пока",
    "and", "but", "or", "which", "that", "when", "if", "because", "while", "although",
)

_PARA_RE = re.compile(r"\n[ \t]*\n\s*")
_SENT_RE = re.compile(r"[.!?…;:]+[\"'»”’)\]]*")
_CLAUSE_AFTER_RE = re.compile(r"[,—–]\s+")
_CLAUSE_BEFORE_RE = re.compile(r"\s(?=(?:%s)\b)" % "|".join(CONJUNCTIONS), re.IGNORECASE)
_WORD_RE = re.compile(r"\s+")
_TOKEN_BEFORE_RE = re.compile(r"([\w.\-]+)$")
_BAD_START_RE = re.compile(r"^(?:[,;]\s*|(?:и|а|но)\s+)", re.IGNORECASE)
_MULTISPACE_RE = re.compile(r"[ \t ]{2,}")


class _Splitter:
    def __init__(self, text: str, limit: int) -> None:
        self.text = text
        self.limit = limit
        self.levels: list[Callable[[Span], list[int]]] = [
            self._para_cuts, self._sentence_cuts, self._clause_cuts, self._word_cuts,
        ]

    def size(self, span: Span) -> int:
        return len(self.text[span[0]:span[1]].strip())

    def _para_cuts(self, span: Span) -> list[int]:
        s, e = span
        return [m.end() for m in _PARA_RE.finditer(self.text, s, e)]

    def _sentence_cuts(self, span: Span) -> list[int]:
        s, e = span
        text = self.text
        cuts: list[int] = []
        for m in _SENT_RE.finditer(text, s, e):
            end = m.end()
            if end < e and not text[end].isspace():
                continue
            if m.group(0) == "." and not self._is_sentence_end_dot(m.start(), end, e):
                continue
            while end < e and text[end].isspace():
                end += 1
            if s < end < e:
                cuts.append(end)
        return cuts

    def _is_sentence_end_dot(self, dot_pos: int, after: int, span_end: int) -> bool:
        tok_match = _TOKEN_BEFORE_RE.search(self.text, max(0, dot_pos - 20), dot_pos)
        if not tok_match:
            return True
        token = tok_match.group(1)
        low = token.lower().lstrip(".")
        next_char = self._next_visible(after, span_end)
        if len(token) == 1 and token.isupper() and token.isalpha():
            return False
        if low in ABBREVIATIONS:
            if low in LIST_END_ABBR and next_char.isupper():
                return True
            return False
        return True

    def _next_visible(self, pos: int, end: int) -> str:
        while pos < end and self.text[pos].isspace():
            pos += 1
        return self.text[pos] if pos < end else ""

    def _clause_cuts(self, span: Span) -> list[int]:
        s, e = span
        cuts = {m.end() for m in _CLAUSE_AFTER_RE.finditer(self.text, s, e)}
        cuts |= {m.start() + 1 for m in _CLAUSE_BEFORE_RE.finditer(self.text, s, e)}
        return sorted(c for c in cuts if s < c < e)

    def _word_cuts(self, span: Span) -> list[int]:
        s, e = span
        return [m.end() for m in _WORD_RE.finditer(self.text, s, e) if s < m.end() < e]

    @staticmethod
    def _cut(span: Span, points: list[int]) -> list[Span]:
        bounds = [span[0], *points, span[1]]
        return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]

    # cut at the strongest boundary that works, refine pieces that are still too long, then pack greedily
    def split(self, span: Span, level: int = 0) -> list[Span]:
        if self.size(span) <= self.limit:
            return [span]
        if level >= len(self.levels):
            return self._hard_cut(span)
        pieces = self._cut(span, self.levels[level](span))
        if len(pieces) <= 1:
            return self.split(span, level + 1)
        refined: list[Span] = []
        for piece in pieces:
            refined.extend(self.split(piece, level + 1) if self.size(piece) > self.limit else [piece])
        return self.pack(refined)

    # only reached for single "words" longer than the limit, e.g. a giant url
    def _hard_cut(self, span: Span) -> list[Span]:
        s, e = span
        return [(i, min(i + self.limit, e)) for i in range(s, e, self.limit)]

    def pack(self, spans: list[Span]) -> list[Span]:
        out: list[Span] = []
        cur: Span | None = None
        for span in spans:
            if cur is None:
                cur = span
            elif self.size((cur[0], span[1])) <= self.limit:
                cur = (cur[0], span[1])
            else:
                out.append(cur)
                cur = span
        if cur is not None:
            out.append(cur)
        return out

    def strip_span(self, span: Span) -> Span:
        s, e = span
        while s < e and self.text[s].isspace():
            s += 1
        while e > s and self.text[e - 1].isspace():
            e -= 1
        return s, e

    def fix_bad_starts(self, spans: list[Span]) -> list[Span]:
        spans = [self.strip_span(sp) for sp in spans]
        for i in range(1, len(spans)):
            s, e = spans[i]
            m = _BAD_START_RE.match(self.text, s, e)
            if not m:
                continue
            new_start = m.end()
            prev = (spans[i - 1][0], new_start)
            if self.size(prev) <= self.limit and new_start < e:
                spans[i - 1] = self.strip_span(prev)
                spans[i] = self.strip_span((new_start, e))
        return [sp for sp in spans if sp[1] > sp[0]]


def normalize_chunk_text(text: str) -> str:
    text = _MULTISPACE_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def effective_limit(max_chars: int) -> int:
    return max(MIN_LIMIT, int(max_chars * (1 - SAFETY_MARGIN)))


def split_text(text: str, max_chars: int, hash_salt: str = "") -> list[Chunk]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    sp = _Splitter(text, effective_limit(max_chars))
    spans = sp.fix_bad_starts(sp.split((0, len(text))))
    chunks: list[Chunk] = []
    for start, end in spans:
        body = normalize_chunk_text(text[start:end])
        if not body:
            continue
        chunks.append(Chunk(
            index=len(chunks) + 1,
            text=body,
            start=start,
            end=end,
            hash=chunk_hash(body, hash_salt),
        ))
    return chunks
