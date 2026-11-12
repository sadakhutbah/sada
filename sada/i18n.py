"""Server messages in the reader's language.

Source strings are written in English in the code and wrapped in ``_()``::

    from sada.i18n import _
    raise Invalid(_("The orchestrator address must start with http:// or https://."))
    _ok(_("Bucket saved: {name}.", name=bucket))

Translations live in ``sada/locales/<lang>/*.json``: flat objects mapping the English source string to
its translation (placeholders ``{name}`` kept as they are). Every JSON file of a language directory is
merged, so each area of the code can keep its own file. A missing translation falls back to English.

The language is per request (``X-Sada-Lang`` header sent by the front end, then ``Accept-Language``),
and otherwise the server default ``SADA_LANG`` (``en`` when unset). Background work outside a request
uses the server default.
"""

from __future__ import annotations

import contextvars
import json
import os
from functools import cache
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / "locales"
SUPPORTED = ("en", "fr", "ar")

_lang: contextvars.ContextVar[str | None] = contextvars.ContextVar("sada_lang", default=None)


def default_lang() -> str:
    lang = (os.environ.get("SADA_LANG") or "en").strip().lower()[:2]
    return lang if lang in SUPPORTED else "en"


def current() -> str:
    return _lang.get() or default_lang()


def set_lang(lang: str | None) -> contextvars.Token:
    return _lang.set(normalize(lang))


def reset(token: contextvars.Token) -> None:
    _lang.reset(token)


def normalize(value: str | None) -> str | None:
    """``fr-FR,fr;q=0.9,en;q=0.8`` -> ``fr``; unknown or empty -> None."""
    if not value:
        return None
    for part in value.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in SUPPORTED:
            return code
    return None


@cache
def catalog(lang: str) -> dict[str, str]:
    out: dict[str, str] = {}
    d = LOCALES / lang
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            out.update(json.loads(f.read_text(encoding="utf-8")))
    return out


def translate(text: str, lang: str | None = None, **kwargs) -> str:
    lang = lang or current()
    s = text if lang == "en" else catalog(lang).get(text, text)
    return s.format(**kwargs) if kwargs else s


def _(text: str, **kwargs) -> str:
    """Translate ``text`` (English source) into the current language, then fill its placeholders."""
    return translate(text, None, **kwargs)


class LanguageMiddleware:
    """Pure ASGI middleware: sets the request language for everything the request runs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        lang = normalize(headers.get("x-sada-lang")) or normalize(headers.get("accept-language"))
        token = _lang.set(lang)
        try:
            await self.app(scope, receive, send)
        finally:
            _lang.reset(token)
