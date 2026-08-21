"""
Match HZZ listing titles to the official UV (Upravno vijeće) TTR-exemption list.

This is a separate inclusion track from foreigner-keyword scoring:
  - keyword score = the ad's wording mentions third-country hiring
  - UV match = the job title looks like an occupation that may skip the
    labour-market test (capability / procedure), not a sponsorship guarantee

The list is a static transcription of the MUP/HZZ PDF in data/uv-occupations.json.
HZZ ads have no NKZ codes here; matching is title text only (slash-gendered
forms like "PROGRAMER / KA" included). Browse category labels are too coarse
and are not used.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

import config

def _nfc_fold(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFC", text).casefold()


_GENDER_PARTICLES = {"ica", "ka", "i", "m", "ž", "z"}


def normalize_title(text: str) -> str:
    """Collapse HZZ gender slashes so 'PROGRAMER / KA' → 'programer'."""
    folded = _nfc_fold(text)
    folded = folded.replace("/", " ").replace("-", " ").replace("–", " ")
    folded = re.sub(r"[^\w\s]", " ", folded, flags=re.UNICODE)
    tokens = [
        tok
        for tok in re.split(r"\s+", folded.strip())
        if tok and tok not in _GENDER_PARTICLES
    ]
    return " ".join(tokens)


def _occupation_needles(title_hr: str) -> list[str]:
    """Needles from an official title. Longer phrases first."""
    stripped = re.sub(r"\([^)]*\)", " ", title_hr)
    parts = re.split(r"\s+i\s+", stripped, flags=re.IGNORECASE)
    needles: list[str] = []
    for part in parts:
        tokens = part.strip().split()
        if not tokens:
            continue
        first_variants = [v for v in re.split(r"[/]", tokens[0]) if v.strip()]
        rest = tokens[1:]
        rest_n = normalize_title(" ".join(rest)) if rest else ""
        for variant in first_variants:
            stem = normalize_title(variant)
            if not stem:
                continue
            if rest_n:
                needles.append(f"{stem} {rest_n}")
            else:
                needles.append(stem)
    needles.sort(key=len, reverse=True)
    # unique, keep order
    seen: set[str] = set()
    out: list[str] = []
    for n in needles:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _haystack_has_needle(haystack: str, needle: str) -> bool:
    padded = f" {haystack} "
    if " " in needle:
        return f" {needle} " in padded
    # Single stem: allow the usual Croatian gendered endings after the stem.
    return re.search(
        rf"(?<!\w){re.escape(needle)}(?:ica|ka)?(?!\w)",
        padded,
    ) is not None


class UvOccupationList:
    def __init__(self, path: Path | None = None):
        self.path = path or config.UV_LIST_PATH
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.meta: dict = raw["listMeta"]
        self.occupations: list[dict] = []
        self._needles: list[tuple[str, list[str]]] = []
        for occ in raw["occupations"]:
            regions = occ.get("regions") or []
            if config.UV_REGION not in regions and "all" not in regions:
                continue
            needles = [n for n in _occupation_needles(occ["titleHr"]) if len(n) >= 4]
            if not needles:
                continue
            self.occupations.append(occ)
            self._needles.append((occ["id"], needles))

    def match_ids(self, title: str) -> list[str]:
        hay = normalize_title(title)
        if not hay:
            return []
        hits: list[str] = []
        for occ_id, needles in self._needles:
            if any(_haystack_has_needle(hay, needle) for needle in needles):
                hits.append(occ_id)
        return hits

    def occupation_by_id(self, occ_id: str) -> dict | None:
        for occ in self.occupations:
            if occ["id"] == occ_id:
                return occ
        return None


_LIST: UvOccupationList | None = None


def load_uv_list(path: Path | None = None) -> UvOccupationList:
    global _LIST
    if path is not None:
        return UvOccupationList(path)
    if _LIST is None:
        _LIST = UvOccupationList()
    return _LIST


def match_shortage_occupations(title: str, uv_list: UvOccupationList | None = None) -> list[str]:
    return (uv_list or load_uv_list()).match_ids(title)


def apply_shortage(listing, uv_list: UvOccupationList | None = None) -> list[str]:
    hits = match_shortage_occupations(listing.title, uv_list)
    listing.shortage_occupations = hits
    listing.shortage_match = bool(hits)
    return hits


def verified_age_days(meta: dict | None = None, today: date | None = None) -> int:
    meta = meta or load_uv_list().meta
    verified = date.fromisoformat(meta["verifiedDate"])
    today = today or date.today()
    return (today - verified).days


def assert_list_not_stale(today: date | None = None) -> None:
    age = verified_age_days(today=today)
    if age > config.UV_STALE_AFTER_DAYS:
        raise SystemExit(
            f"UV occupation list verifiedDate is {age} days old "
            f"(limit {config.UV_STALE_AFTER_DAYS}). Re-open "
            f"{load_uv_list().meta.get('hubUrl')} and bump verifiedDate "
            "or re-ingest occupations. See METHOD.md."
        )


def fetch_source_sha256(url: str | None = None, timeout: int = 30) -> str:
    meta = load_uv_list().meta
    target = url or meta["sourceUrl"]
    req = Request(target, headers={"User-Agent": config.USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return hashlib.sha256(body).hexdigest()


def assert_source_pdf_unchanged() -> None:
    """Fail if the official PDF bytes no longer match listMeta.sourceSha256."""
    meta = load_uv_list().meta
    expected = (meta.get("sourceSha256") or "").strip().lower()
    if not expected:
        raise SystemExit("listMeta.sourceSha256 is missing; cannot verify PDF.")
    actual = fetch_source_sha256()
    if actual != expected:
        raise SystemExit(
            "Official UV PDF changed (SHA-256 mismatch).\n"
            f"  url: {meta['sourceUrl']}\n"
            f"  stored: {expected}\n"
            f"  live:   {actual}\n"
            "Re-transcribe data/uv-occupations.json from the new PDF, "
            "update edition / verifiedDate / sourceSha256. See METHOD.md."
        )


def list_meta_public() -> dict:
    meta = load_uv_list().meta
    return {
        "edition": meta.get("edition"),
        "verifiedDate": meta.get("verifiedDate"),
        "sourceUrl": meta.get("sourceUrl"),
        "hubUrl": meta.get("hubUrl"),
        "sourceName": meta.get("sourceName"),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="UV occupation list helpers")
    parser.add_argument(
        "--check-stale",
        action="store_true",
        help="Exit nonzero if verifiedDate is older than UV_STALE_AFTER_DAYS",
    )
    parser.add_argument(
        "--check-pdf",
        action="store_true",
        help="Download sourceUrl and compare SHA-256 to listMeta.sourceSha256",
    )
    args = parser.parse_args()
    if args.check_pdf:
        assert_source_pdf_unchanged()
        print("UV PDF hash matches listMeta.sourceSha256.")
    if args.check_stale or not args.check_pdf:
        assert_list_not_stale()
        print("UV list verifiedDate is within UV_STALE_AFTER_DAYS.")
