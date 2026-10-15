import re

import pytest

from app.core.text_splitter import effective_limit, split_text

SAMPLE = (
    "Глава 1.\n\n"
    "А. С. Пушкин родился в Москве, т.е. в г. Москва на ул. Немецкой. Он писал стихи, прозу и т.д. "
    "Потом он уехал. Mr. Smith и Dr. Brown пришли в 10.30! Правда? Да… Конечно.\n\n"
    + "Длинное предложение без точек, которое идёт и идёт, а потом продолжается, но не кончается, "
      "потому что автор любит запятые и союзы, когда пишет, если есть настроение " * 6
    + ".\n\n" + "слово " * 300
)


def squash(s):
    return re.sub(r"\s", "", s)


@pytest.mark.parametrize("limit", [200, 500, 3000])
def test_chunks_fit_limit(limit):
    chunks = split_text(SAMPLE, limit)
    assert chunks
    assert max(len(c.text) for c in chunks) <= effective_limit(limit)


@pytest.mark.parametrize("limit", [200, 500])
def test_no_text_lost_or_reordered(limit):
    chunks = split_text(SAMPLE, limit)
    assert squash("".join(c.text for c in chunks)) == squash(SAMPLE)


def test_words_are_not_cut():
    words = set(SAMPLE.split())
    for c in split_text(SAMPLE, 200):
        for w in c.text.split():
            assert w in words


def test_chunk_does_not_start_with_conjunction():
    for c in split_text(SAMPLE, 200)[1:]:
        assert not c.text.lower().startswith((", ", "и ", "а ", "но "))


def test_abbreviations_and_initials_dont_end_sentence():
    text = "А. С. Пушкин жил на ул. Мойке, т.е. в центре. Mr. Smith тоже. " * 3
    chunks = split_text(text, 120)
    for c in chunks:
        assert not c.text.endswith(("А.", "С.", "ул.", "т.е.", "Mr."))


def test_positions_point_into_original():
    for c in split_text(SAMPLE, 300):
        assert SAMPLE[c.start:c.end].split() == c.text.split()


def test_indexes_and_hash():
    a = split_text(SAMPLE, 300, "voice-a")
    b = split_text(SAMPLE, 300, "voice-b")
    assert [c.index for c in a] == list(range(1, len(a) + 1))
    assert a[0].hash != b[0].hash


def test_giant_word_is_hard_cut():
    url = "https://example.com/" + "x" * 1000
    chunks = split_text(url, 300)
    assert len(chunks) > 1
    assert "".join(c.text for c in chunks) == url


def test_empty_text():
    assert split_text("   \n\n ", 500) == []
