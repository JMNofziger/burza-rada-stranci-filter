"""HR→EN translation for Telegram (same MyMemory endpoint as the jobs board).

Fails open: if the API errors or returns nothing useful, callers keep the
original Croatian string. Results are cached in SQLite so daily digests do
not re-hit the API for the same title/employer.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

import config

log = logging.getLogger(__name__)

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
LANGPAIR = "hr|en"
MAX_QUERY_CHARS = 450
REQUEST_DELAY_SECONDS = 0.2
_QUOTA_MARKERS = ("quota", "invalid language pair", "mymemory warning", "free translations")


def _useful_translation(source: str, translated: str) -> str | None:
    text = (translated or "").strip()
    src = (source or "").strip()
    if not text or not src:
        return None
    if text.casefold() == src.casefold():
        return None
    lowered = text.casefold()
    if any(marker in lowered for marker in _QUOTA_MARKERS):
        return None
    return text


def fetch_mymemory(text: str, timeout: int = 15) -> str | None:
    query = (text or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        return None
    resp = requests.get(
        MYMEMORY_URL,
        params={"q": query, "langpair": LANGPAIR},
        timeout=timeout,
        headers={"User-Agent": config.USER_AGENT},
    )
    if resp.status_code != 200:
        log.warning("MyMemory HTTP %s for %r", resp.status_code, query[:80])
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    translated = ((data.get("responseData") or {}).get("translatedText")) or ""
    return _useful_translation(query, translated)


class Translator:
    """hr→en helper with optional SQLite cache. `fetch` is injectable for tests."""

    def __init__(
        self,
        store=None,
        fetch: Callable[[str], str | None] = fetch_mymemory,
        delay: float = REQUEST_DELAY_SECONDS,
    ):
        self.store = store
        self.fetch = fetch
        self.delay = delay
        self._last_fetch_at = 0.0

    def hr_en(self, text: str) -> str | None:
        source = (text or "").strip()
        if not source:
            return None
        if self.store is not None:
            hit, cached = self.store.get_translation(source, LANGPAIR)
            if hit:
                return cached or None
        self._throttle()
        try:
            translated = self.fetch(source)
        except Exception:
            log.exception("Translation failed for %r", source[:80])
            translated = None
        if self.store is not None:
            self.store.set_translation(source, LANGPAIR, translated or "")
        return translated

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        wait = self.delay - (time.monotonic() - self._last_fetch_at)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch_at = time.monotonic()
