from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

import scoring
from http_client import extract_postback, harvest_form_state
from scraper import (
    _grad_zagreb_postback,
    _next_page_target,
    _occupation_categories,
    fetch_list_page,
    parse_hr_date,
)
from tests.fixtures import BROWSE_PAGE_HTML, LIST_PAGE_HTML


class HarvestFormStateTests(unittest.TestCase):
    def test_skips_submit_buttons(self):
        soup = BeautifulSoup(LIST_PAGE_HTML, "lxml")
        state = harvest_form_state(soup)
        self.assertIn("__VIEWSTATE", state)
        self.assertIn("ctl00$MainContent$ddlPageSize", state)
        self.assertEqual(state["ctl00$MainContent$ddlPageSize"], "75")
        self.assertNotIn("ctl00$MainContent$btnTrazilica", state)


class ParseListPageTests(unittest.TestCase):
    def test_parses_title_employer_location_deadline(self):
        soup = BeautifulSoup(LIST_PAGE_HTML, "lxml")
        listings = fetch_list_page(None, soup)
        self.assertEqual(len(listings), 2)
        first = listings[0]
        self.assertEqual(first.web_sifra, "165734230")
        self.assertEqual(first.title, "INZENJER BIOMEDICINE")
        self.assertEqual(first.employer, "Dominus")
        self.assertEqual(first.location_raw, "ZAGREB")
        self.assertEqual(first.deadline_date.isoformat(), "2026-09-21")
        self.assertIn("WebSifra=165734230", first.detail_url)

    def test_open_ended_deadline(self):
        soup = BeautifulSoup(LIST_PAGE_HTML, "lxml")
        listings = fetch_list_page(None, soup)
        self.assertIsNone(listings[1].deadline_date)
        self.assertEqual(listings[1].location_raw, "SESVETE")

    def test_next_page_target(self):
        soup = BeautifulSoup(LIST_PAGE_HTML, "lxml")
        self.assertEqual(
            _next_page_target(soup),
            "ctl00$MainContent$gwSearch$ctl13$ctl04",
        )


class BrowsePageTests(unittest.TestCase):
    def test_grad_zagreb_radio_not_confused_with_zagrebacka(self):
        soup = BeautifulSoup(BROWSE_PAGE_HTML, "lxml")
        target, value = _grad_zagreb_postback(soup)
        self.assertEqual(value, "4")
        self.assertEqual(target, "ctl00$MainContent$rblZupanija$4")

    def test_skips_zero_count_categories(self):
        soup = BeautifulSoup(BROWSE_PAGE_HTML, "lxml")
        cats = _occupation_categories(soup)
        labels = [label for _, _, label in cats]
        self.assertEqual(len(cats), 2)
        self.assertTrue(any("Informatički" in label for label in labels))
        self.assertTrue(any("Zdravstvo" in label for label in labels))
        self.assertFalse(any("Ugostitelji" in label for label in labels))


class ScoringTests(unittest.TestCase):
    def test_sesvete_counts_as_zagreb_when_county_filtered(self):
        self.assertEqual(scoring.score_location("SESVETE", in_zagreb_county=True), 1)
        self.assertEqual(scoring.score_location("SESVETE"), 0)

    def test_centre_boost(self):
        self.assertEqual(
            scoring.score_location("ZAGREB", "rad u centru, Ilica 12", in_zagreb_county=True),
            2,
        )


class HelpersTests(unittest.TestCase):
    def test_extract_postback_settimeout_escaped(self):
        href = r"javascript:setTimeout('__doPostBack(\'ctl00$MainContent$rblZupanija$4\',\'\')', 0)"
        self.assertEqual(
            extract_postback(href),
            ("ctl00$MainContent$rblZupanija$4", ""),
        )

    def test_parse_unpadded_date(self):
        self.assertEqual(str(parse_hr_date("21.9.2026.")), "2026-09-21")


if __name__ == "__main__":
    unittest.main()
