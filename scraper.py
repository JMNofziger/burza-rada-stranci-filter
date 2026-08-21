"""
scraper.py
List-page and detail-page parsing.

Two-stage crawl, by design
---------------------------
Real ad text on this portal shows the search/list page carrying only title,
employer and a short location/deadline line -- the full description, where
phrases like "osiguran smještaj" or "dozvola za boravak i rad" actually live,
is only present on each job's detail page (`RadnoMjesto_Ispis.aspx?WebSifra=...`).

Live site notes (confirmed 2026-08-21)
--------------------------------------
- Landing page is an occupation-category browser, not a result grid.
- Filter županija via radio `ctl00$MainContent$rblZupanija` value `4` = GRAD ZAGREB.
- "Svi poslovi" (`btnOblikRada_All`) opens a result grid, but that grid is
  capped at 300 rows. Occupation categories on the browse page sum to ~1,080
  Grad Zagreb jobs, so we iterate `lnkKategorija` instead of the capped dump.
- Result grid: `#ctl00_MainContent_gwSearch`, rows contain `a.TitleLink`.
- Pager: `ul.pagination` with `__doPostBack('ctl00$MainContent$gwSearch$ctlNN$ctlMM')`.
- Never include the "Povratak na tražilicu" submit button in postback payloads.
- Politeness delay: 1 s between sequential requests (`REQUEST_DELAY_SECONDS`).
- Detail body: `#ctl00_MainContent_pnlAjaxBlock`.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from collections.abc import Callable
from typing import Iterator, Optional

from bs4 import BeautifulSoup

import config
from http_client import (
    extract_postback,
    harvest_form_state,
    submit_postback,
    warm_up,
    _fix_encoding,
)

log = logging.getLogger(__name__)

# Confirmed against live DOM (see module docstring).
LIST_GRID_SELECTOR = "#ctl00_MainContent_gwSearch"
LIST_TITLE_SELECTOR = "a.TitleLink"
DETAIL_TEXT_SELECTOR = "#ctl00_MainContent_pnlAjaxBlock"
ZUPANIJA_RADIO_SELECTOR = "#ctl00_MainContent_rblZupanija input[type=radio]"
CATEGORY_EVENT_NEEDLE = "lnkKategorija"

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
    category_label: str = ""


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
    grid = soup.select_one(LIST_GRID_SELECTOR)
    rows = grid.find_all("tr") if grid is not None else []
    if not rows:
        log.warning(
            "0 rows matched %s -- selector is likely stale, verify against live markup.",
            LIST_GRID_SELECTOR,
        )
    seen_on_page: set[str] = set()
    for row in rows:
        try:
            title_el = row.select_one(LIST_TITLE_SELECTOR)
            if title_el is None:
                continue
            web_sifra = _extract_web_sifra(row)
            if not web_sifra or web_sifra in seen_on_page:
                continue
            seen_on_page.add(web_sifra)
            listing = JobListing(
                web_sifra=str(web_sifra),
                title=_clean(title_el.get_text()),
                employer=_clean(_labeled_span(row, "PosNazivLabel")),
                location_raw=_clean(_labeled_span(row, "MjeNazivLabel")),
                deadline_raw=_clean(_labeled_span(row, "RadMjeRokPrijaveLabel")),
                detail_url=config.DETAIL_URL_TEMPLATE.format(web_sifra=web_sifra),
            )
            listing.deadline_date = parse_hr_date(listing.deadline_raw)
            listings.append(listing)
        except Exception:
            # One malformed row must never abort the whole run.
            log.exception("Failed to parse a listing row; skipping it.")
            continue
    return listings


def _labeled_span(row, id_suffix: str) -> str:
    el = row.select_one(f"span[id$={id_suffix}]")
    return el.get_text() if el else ""


def _extract_web_sifra(row) -> Optional[str]:
    """Pull WebSifra out of a detail link's href."""
    link = row.find("a", href=re.compile(r"WebSifra=(\d+)"))
    if not link:
        return None
    match = re.search(r"WebSifra=(\d+)", link.get("href") or "")
    return match.group(1) if match else None


def fetch_detail(session, listing: JobListing) -> Optional[JobListing]:
    """Fetch and attach full description text for one listing. Never raises."""
    try:
        time.sleep(config.REQUEST_DELAY_SECONDS)
        resp = session.get(listing.detail_url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        _fix_encoding(resp)
        soup = BeautifulSoup(resp.text, "lxml")
        body = soup.select_one(DETAIL_TEXT_SELECTOR)
        listing.description = _clean(body.get_text(separator="\n")) if body else ""
        if not listing.description:
            log.warning(
                "Empty description for WebSifra=%s -- DETAIL_TEXT_SELECTOR likely stale.",
                listing.web_sifra,
            )
        loc = soup.select_one("#ctl00_MainContent_lblMjestoRada")
        if loc and not listing.location_raw:
            listing.location_raw = _clean(loc.get_text())
        emp = soup.select_one("#ctl00_MainContent_lblNazivPoslodavca")
        if emp and not listing.employer:
            listing.employer = _clean(emp.get_text())
        deadline = soup.select_one("#ctl00_MainContent_lblVrijediDo")
        if deadline and not listing.deadline_raw:
            listing.deadline_raw = _clean(deadline.get_text())
            listing.deadline_date = parse_hr_date(listing.deadline_raw)
        return listing
    except Exception:
        log.exception("Failed to fetch detail page for WebSifra=%s", listing.web_sifra)
        return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _postback(session, soup: BeautifulSoup, event_target: str, extra: dict | None = None) -> BeautifulSoup:
    state = harvest_form_state(soup)
    overrides = {"__EVENTTARGET": event_target, "__EVENTARGUMENT": ""}
    if extra:
        overrides.update(extra)
    time.sleep(config.REQUEST_DELAY_SECONDS)
    return submit_postback(session, config.LIST_URL, state, overrides)


def _grad_zagreb_postback(soup: BeautifulSoup) -> tuple[str, str]:
    """Return (event_target, radio_value) for the GRAD ZAGREB county radio."""
    wanted = config.ZUPANIJA_GRAD_ZAGREB_LABEL.casefold()
    for inp in soup.select(ZUPANIJA_RADIO_SELECTOR):
        label = soup.find("label", {"for": inp.get("id")})
        text = _clean(label.get_text() if label else "")
        if text.casefold() != wanted:
            continue
        parsed = extract_postback(inp.get("onclick") or "")
        target = parsed[0] if parsed else f"{config.ZUPANIJA_FIELD}${inp.get('value')}"
        return target, inp.get("value") or config.ZUPANIJA_GRAD_ZAGREB_VALUE
    log.warning(
        "GRAD ZAGREB radio not found; falling back to configured value %s",
        config.ZUPANIJA_GRAD_ZAGREB_VALUE,
    )
    return (
        f"{config.ZUPANIJA_FIELD}${config.ZUPANIJA_GRAD_ZAGREB_VALUE}",
        config.ZUPANIJA_GRAD_ZAGREB_VALUE,
    )


def _occupation_categories(soup: BeautifulSoup) -> list[tuple[str, int, str]]:
    """(event_target, listed_count, label) for each occupation category with jobs."""
    found: list[tuple[str, int, str]] = []
    for a in soup.find_all("a", href=True):
        parsed = extract_postback(a["href"])
        if not parsed or CATEGORY_EVENT_NEEDLE not in parsed[0]:
            continue
        label = _clean(a.get_text())
        count_match = re.search(r"(\d+)\s*$", label)
        count = int(count_match.group(1)) if count_match else 1
        if count <= 0:
            continue
        found.append((parsed[0], count, label))
    return found


def _set_page_size(session, soup: BeautifulSoup) -> BeautifulSoup:
    select = soup.select_one(f"select[name='{config.PAGE_SIZE_FIELD}']")
    if select is None:
        return soup
    selected = select.find("option", selected=True)
    if selected is not None and selected.get("value") == config.LIST_PAGE_SIZE:
        return soup
    return _postback(
        session,
        soup,
        config.PAGE_SIZE_EVENT_TARGET,
        {config.PAGE_SIZE_FIELD: config.LIST_PAGE_SIZE},
    )


def _next_page_target(soup: BeautifulSoup) -> Optional[str]:
    pager = soup.select_one(f"{LIST_GRID_SELECTOR} ul.pagination")
    if pager is None:
        return None
    active = pager.select_one("li.active")
    if active is None:
        return None
    try:
        current = int(_clean(active.get_text()))
    except ValueError:
        return None
    for a in pager.find_all("a", href=True):
        text = _clean(a.get_text())
        if text.isdigit() and int(text) == current + 1:
            parsed = extract_postback(a["href"])
            return parsed[0] if parsed else None
    return None


def iter_zagreb_candidates(
    session,
    skip_category: Callable[[str, str], bool] | None = None,
    on_category_complete: Callable[[str, str], None] | None = None,
) -> Iterator[JobListing]:
    """
    Filter server-side to Grad Zagreb, walk each occupation category (avoids
    the 300-row "Svi poslovi" cap), and yield every listing. Callers still
    need fetch_detail() to score foreign-friendliness.

    skip_category(event_target, label) -> True skips a finished occupation
    group so a resumed full-scrape list walk does not re-paginate it.
    on_category_complete is invoked only after every page of that group.
    """
    soup = warm_up(session)
    county_target, county_value = _grad_zagreb_postback(soup)
    soup = _postback(
        session,
        soup,
        county_target,
        {config.ZUPANIJA_FIELD: county_value},
    )
    categories = _occupation_categories(soup)
    if config.MAX_CATEGORIES:
        categories = categories[: config.MAX_CATEGORIES]
    log.info(
        "Grad Zagreb browse page: %d occupation categories (listed job sum ~%d).",
        len(categories),
        sum(count for _, count, _ in categories),
    )
    if not categories:
        log.warning("No occupation categories found after county filter -- markup may have changed.")
        return

    browse_soup = soup
    seen: set[str] = set()
    skipped = 0
    for event_target, listed_count, label in categories:
        if skip_category and skip_category(event_target, label):
            skipped += 1
            log.info("Skipping completed category %s", label)
            continue
        log.info("Category %s (listed %d)", label, listed_count)
        # Always post back from the county-filtered browse snapshot so category
        # LinkButtons are present. Listing pages do not contain them.
        soup = _postback(
            session,
            browse_soup,
            event_target,
            {config.ZUPANIJA_FIELD: county_value},
        )
        soup = _set_page_size(session, soup)
        page = 1
        while soup is not None and page <= config.MAX_PAGES_PER_CATEGORY:
            for listing in fetch_list_page(session, soup):
                if listing.web_sifra in seen:
                    continue
                seen.add(listing.web_sifra)
                listing.category_label = label
                yield listing
            next_target = _next_page_target(soup)
            if not next_target:
                break
            soup = _postback(session, soup, next_target)
            page += 1
        else:
            if page > config.MAX_PAGES_PER_CATEGORY:
                log.warning(
                    "Hit MAX_PAGES_PER_CATEGORY (%d) on %s; remaining rows skipped.",
                    config.MAX_PAGES_PER_CATEGORY,
                    label,
                )
        if on_category_complete:
            on_category_complete(event_target, label)
    log.info(
        "List crawl finished: %d unique Grad Zagreb WebSifra values (%d categories skipped).",
        len(seen),
        skipped,
    )
