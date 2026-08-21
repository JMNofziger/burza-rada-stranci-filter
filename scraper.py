"""
scraper.py
List-page and detail-page parsing.

Two-stage crawl, by design
---------------------------
Real ad text on this portal (confirmed from live example listings) shows the
search/list page carrying only title, employer and a short location/deadline
line -- the full description, where phrases like "osiguran smještaj" or
"dozvola za boravak i rad" actually live, is only present on each job's
detail page (`RadnoMjesto_Ispis.aspx?WebSifra=...`). Filtering on list-page
text alone will silently miss most true positives. So:

  1. list page  -> cheap row parse (WebSifra, title, employer, raw location,
                    raw deadline)
  2. cheap filter -> keep rows whose raw location line mentions Zagreb at all
                      (skip obvious non-Zagreb rows before spending a request
                      on them)
  3. detail page -> fetch full text, run the real foreign-friendly + centre
                     scoring against it

NOTE: the exact table/row CSS selectors below (`.rezultati-tablica`, etc.)
are placeholders -- the live grid markup could not be confirmed (see
README "Known unknowns"). Run `discover_form_fields` / inspect the page
once in a browser and adjust `LIST_ROW_SELECTOR` / `DETAIL_TEXT_SELECTOR`
accordingly before relying on this in production.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Optional

from bs4 import BeautifulSoup

import config
from http_client import build_session, warm_up, _fix_encoding

log = logging.getLogger(__name__)

# TODO/VERIFY: confirm against live DOM
LIST_ROW_SELECTOR = "table.rezultati-tablica tr[data-web-sifra]"
DETAIL_TEXT_SELECTOR = "#opisPosla, .opis-radnog-mjesta, .job-description"

DATE_PATTERNS = [
    "%d.%m.%Y.",  # "27.12.2023."  -- the standard HR format seen live
    "%d.%m.%Y",
]

# Ads with no fixed closing date ("do popune" / "do isteka natječaja" / "trajno")
NO_DEADLINE_MARKERS = ("do popune", "trajno", "do isteka")


@dataclass
class JobListing:
    web_sifra: str
    title: str
    employer: str
    location_raw: str
    deadline_raw: str
    detail_url: str
    deadline_date: Optional[date] = None
    description: str = ""
    foreign_score: int = 0
    matched_keywords: list = field(default_factory=list)
    location_score: int = 0


def parse_hr_date(raw: str) -> Optional[date]:
    """Parse a Croatian dd.mm.yyyy. date; return None for open-ended ads."""
    if not raw:
        return None
    cleaned = raw.strip()
    lowered = cleaned.lower()
    if any(marker in lowered for marker in NO_DEADLINE_MARKERS):
        return None
    for fmt in DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    log.warning("Could not parse deadline date: %r", raw)
    return None


def fetch_list_page(session, soup: BeautifulSoup) -> list[JobListing]:
    """Parse job rows out of a single list-page response."""
    listings: list[JobListing] = []
    rows = soup.select(LIST_ROW_SELECTOR)
    if not rows:
        log.warning(
            "0 rows matched LIST_ROW_SELECTOR (%s) -- selector is likely stale, "
            "verify against live markup.",
            LIST_ROW_SELECTOR,
        )
    for row in rows:
        try:
            web_sifra = row.get("data-web-sifra") or _extract_web_sifra(row)
            if not web_sifra:
                continue
            title_el = row.select_one(".naslov, .job-title") or row
            employer_el = row.select_one(".poslodavac, .employer")
            location_el = row.select_one(".lokacija, .location")
            deadline_el = row.select_one(".rok, .deadline")

            listing = JobListing(
                web_sifra=str(web_sifra),
                title=_clean(title_el.get_text() if title_el else ""),
                employer=_clean(employer_el.get_text() if employer_el else ""),
                location_raw=_clean(location_el.get_text() if location_el else ""),
                deadline_raw=_clean(deadline_el.get_text() if deadline_el else ""),
                detail_url=config.DETAIL_URL_TEMPLATE.format(web_sifra=web_sifra),
            )
            listing.deadline_date = parse_hr_date(listing.deadline_raw)
            listings.append(listing)
        except Exception:
            # One malformed row must never abort the whole run.
            log.exception("Failed to parse a listing row; skipping it.")
            continue
    return listings


def _extract_web_sifra(row) -> Optional[str]:
    """Fallback: pull WebSifra out of a detail link's href if no data attr."""
    link = row.find("a", href=re.compile(r"WebSifra=(\d+)"))
    if not link:
        return None
    match = re.search(r"WebSifra=(\d+)", link["href"])
    return match.group(1) if match else None


def looks_like_zagreb(location_raw: str) -> bool:
    return bool(re.search(config.ZAGREB_PATTERN, location_raw.lower()))


def fetch_detail(session, listing: JobListing) -> Optional[JobListing]:
    """Fetch and attach full description text for one listing. Never raises."""
    try:
        resp = session.get(listing.detail_url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        _fix_encoding(resp)
        soup = BeautifulSoup(resp.text, "lxml")
        body = soup.select_one(DETAIL_TEXT_SELECTOR)
        listing.description = _clean(body.get_text(separator="\n")) if body else ""
        if not listing.description:
            log.warning(
                "Empty description for WebSifra=%s -- DETAIL_TEXT_SELECTOR "
                "likely stale.",
                listing.web_sifra,
            )
        return listing
    except Exception:
        log.exception("Failed to fetch detail page for WebSifra=%s", listing.web_sifra)
        return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def iter_zagreb_candidates(session) -> Iterator[JobListing]:
    """
    Warm up the session, walk the (paginated) list, and yield only rows whose
    raw location text mentions Zagreb -- callers still need to fetch each
    yielded listing's detail page (fetch_detail) to score it properly.

    Pagination is left as a documented gap: with ~7-8k active national
    listings (confirmed live counter) you should filter server-side by
    county/city via the search form rather than paging through everything
    and discarding non-Zagreb rows client-side. See http_client.py /
    discover_form_fields for how to find the right form field once you've
    inspected the live page, then swap this generator's body to submit that
    filtered search instead of iterating the unfiltered list.
    """
    soup = warm_up(session)
    page = 1
    while soup is not None:
        for listing in fetch_list_page(session, soup):
            if looks_like_zagreb(listing.location_raw):
                yield listing
        soup = _fetch_next_page(session, soup, page)
        page += 1
        time.sleep(config.REQUEST_DELAY_SECONDS)


def _fetch_next_page(session, soup: BeautifulSoup, current_page: int) -> Optional[BeautifulSoup]:
    """
    TODO/VERIFY: wire this to the real pager control once identified (it will
    be an __EVENTTARGET postback, e.g. targeting a LinkButton named something
    like 'ctl00$cphMain$gridPaging$ctl02'). Returning None here stops the
    crawl after the first page, which is a safe default until pagination is
    confirmed rather than silently mis-scraping.
    """
    log.info("Pagination not yet wired up -- stopping after page %s.", current_page)
    return None
