"""
http_client.py
Session construction and ASP.NET WebForms plumbing.

Why this file exists
---------------------
A plain `requests.get(LIST_URL)` on this site enters a redirect loop. That is
because classic ASP.NET WebForms apps commonly ship a "cookieless session
detection" bounce: the first hit gets redirected to a URL carrying
`?AspxAutoDetectCookieSupport=1`, the server sets a session cookie, and the
*next* request (now carrying that cookie) is allowed through. A stateless
client, or a client that doesn't persist cookies across the redirect chain,
loops forever or gets bounced to an error page. `requests.Session()` handles
this correctly as long as you reuse the same Session object for every call
-- that is the single most important fix relative to the reference code.

The second WebForms wrinkle is server-side pagination and search filtering:
result grids in WebForms are typically posted back via hidden fields
(`__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`, `__EVENTTARGET`,
`__EVENTARGUMENT`) rather than a `?page=2` query string. `harvest_form_state`
below extracts *all* current hidden/text/select values from a form instead of
hardcoding a handful of control names, so that submitting a postback (e.g.
clicking "next page" or a county filter) is done by taking the current form
snapshot and only overriding the one or two fields you actually want to
change -- this is far more resilient to HZZ changing internal control IDs
than hand-typing `ctl00$cphMain$...` names would be.
"""

from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

log = logging.getLogger(__name__)

ASPNET_HIDDEN_FIELDS = (
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
    "__EVENTTARGET",
    "__EVENTARGUMENT",
)


def build_session() -> requests.Session:
    """Create a Session with retries, a real UA, and cookie persistence."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config.USER_AGENT,
            "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.5",
        }
    )
    retry = Retry(
        total=config.MAX_RETRIES,
        backoff_factor=config.RETRY_BACKOFF_FACTOR,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def warm_up(session: requests.Session, url: str = config.LIST_URL) -> BeautifulSoup:
    """
    Perform the initial GET that establishes the ASPX session cookie, and
    return the parsed listing/search page. Must be called once before any
    postback (pagination / filtering) request, and its returned page's
    hidden-field snapshot must be reused as the baseline for the next POST.
    """
    resp = session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    _fix_encoding(resp)
    return BeautifulSoup(resp.text, "lxml")


def _fix_encoding(resp: requests.Response) -> None:
    """
    Legacy ASP.NET sites sometimes mislabel or omit charset, and requests'
    auto-detected `apparent_encoding` can guess wrong for Croatian diacritics
    (č, ć, đ, š, ž). Force UTF-8 unless the server explicitly declared
    something else in the Content-Type header.
    """
    if "charset" not in (resp.headers.get("Content-Type") or "").lower():
        resp.encoding = "utf-8"


def harvest_form_state(soup: BeautifulSoup, form_selector: str = "form") -> dict:
    """
    Snapshot every hidden/text/select input currently on the page's form.
    Use this as the base payload for a postback, then overlay just the
    field(s) you want to change (e.g. the county/city dropdown, or
    __EVENTTARGET for a pager link) before POSTing.
    """
    form = soup.select_one(form_selector)
    if form is None:
        raise ValueError("No <form> found on page -- site markup may have changed.")

    state: dict[str, str] = {}
    for tag in form.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            selected = tag.find("option", selected=True) or tag.find("option")
            state[name] = selected.get("value", "") if selected else ""
        elif tag.get("type") in ("checkbox", "radio"):
            if tag.has_attr("checked"):
                state[name] = tag.get("value", "on")
        else:
            state[name] = tag.get("value", "")
    return state


def submit_postback(
    session: requests.Session,
    url: str,
    base_state: dict,
    overrides: dict,
) -> BeautifulSoup:
    """POST a WebForms postback: base_state merged with overrides."""
    payload = {**base_state, **overrides}
    resp = session.post(url, data=payload, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    _fix_encoding(resp)
    return BeautifulSoup(resp.text, "lxml")


def discover_form_fields(session: requests.Session) -> None:
    """
    One-off diagnostic helper. Run this manually (see README) and inspect the
    printed field names/values before wiring up real search filtering --
    it prints every named input on the search form so you can identify which
    one is the county/city selector and which is the pager control, without
    guessing at control IDs.
    """
    soup = warm_up(session)
    state = harvest_form_state(soup)
    for name, value in sorted(state.items()):
        if name in ASPNET_HIDDEN_FIELDS:
            continue
        print(f"{name!r}: {value!r}")
