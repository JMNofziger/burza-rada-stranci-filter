"""
scoring.py
Foreign-friendliness scoring and location scoring.

Upgrades over naive substring matching:
  - unicodedata normalization + casefold instead of .lower(), so diacritics
    and mixed-case ad text (ALL CAPS titles are common on this portal, e.g.
    "STOLAR-ICA") don't cause missed matches.
  - weighted keywords + a threshold instead of a boolean any(), so one weak
    signal ("terenski rad") doesn't get equal footing with a near-unambiguous
    legal phrase ("dozvola za boravak i rad").
  - a cheap negation guard: a keyword hit is discarded if a negation marker
    ("ne ", "nema ", "bez ", "samo eu državljani" ...) appears shortly before
    it, catching ads that explicitly say they will *not* sponsor a permit.
  - an optional fuzzy-matching pass (rapidfuzz) to catch small inflectional
    variants not in the curated keyword list, at a lower confidence weight.
"""

from __future__ import annotations

import re
import unicodedata

import config

try:
    from rapidfuzz import fuzz

    _HAS_RAPIDFUZZ = True
except ImportError:  # optional dependency
    _HAS_RAPIDFUZZ = False

NEGATION_WINDOW_CHARS = 25
FUZZY_MIN_SCORE = 90  # rapidfuzz partial_ratio threshold, 0-100
FUZZY_WEIGHT = 1


def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return text.casefold()


def score_foreign_friendly(text: str) -> tuple[int, list[str]]:
    """Return (accumulated weight, list of matched phrases) for a job's text."""
    normalized = normalize(text)
    total = 0
    matched: list[str] = []

    for phrase, weight in config.FOREIGNER_KEYWORDS.items():
        idx = normalized.find(normalize(phrase))
        if idx == -1:
            continue
        window_start = max(0, idx - NEGATION_WINDOW_CHARS)
        preceding = normalized[window_start:idx]
        if any(neg in preceding for neg in config.NEGATION_MARKERS):
            continue  # looks like a negated mention, e.g. "ne nudimo radnu dozvolu"
        total += weight
        matched.append(phrase)

    if _HAS_RAPIDFUZZ and total == 0:
        # Only bother with the more expensive fuzzy pass if exact matching
        # found nothing at all -- keeps the common case fast.
        for phrase in config.FOREIGNER_KEYWORDS:
            if fuzz.partial_ratio(normalize(phrase), normalized) >= FUZZY_MIN_SCORE:
                total += FUZZY_WEIGHT
                matched.append(f"~{phrase}")

    return total, matched


def score_location(
    location_raw: str,
    description: str = "",
    *,
    in_zagreb_county: bool = False,
) -> int:
    """0 = not Zagreb, 1 = Zagreb general, 2 = Zagreb city centre.

    `in_zagreb_county=True` when the crawl already applied the Grad Zagreb
    server-side filter -- districts like Sesvete omit the word "Zagreb".
    """
    combined = normalize(f"{location_raw} {description}")
    is_zagreb = in_zagreb_county or bool(re.search(config.ZAGREB_PATTERN, combined))
    if not is_zagreb:
        return 0
    if any(re.search(pat, combined) for pat in config.CENTAR_PATTERNS):
        return 2
    return 1
