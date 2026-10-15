from __future__ import annotations

LANGUAGES = {"en": "English", "ru": "Русский"}

_lang = "en"


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in LANGUAGES else "en"


def language() -> str:
    return _lang


def tr(msg: str, /, **kw: object) -> str:
    if _lang == "ru":
        from app.i18n_ru import RU

        msg = RU.get(msg, msg)
    return msg.format(**kw) if kw else msg
