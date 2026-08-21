from __future__ import annotations

import unittest
from datetime import date

import config
import shortage
from scraper import JobListing


class ShortageMatchTests(unittest.TestCase):
    def setUp(self):
        self.uv = shortage.load_uv_list()

    def test_zagreb_region_excludes_non_zagreb_occupations(self):
        ids = {occ["id"] for occ in self.uv.occupations}
        self.assertIn("kuhar-kuharica", ids)
        self.assertIn("programer-programerka", ids)
        self.assertNotIn("sivac-sivacica-tekstilnih-i-odjevnih-predmeta", ids)
        self.assertNotIn("prodavac-prodavacica-samo-za-period-rada-od-01-05-30-09", ids)

    def test_hzz_slash_gender_forms(self):
        self.assertIn(
            "programer-programerka",
            shortage.match_shortage_occupations("PROGRAMER / KA", self.uv),
        )
        self.assertIn(
            "automehanicar-automehanicarka",
            shortage.match_shortage_occupations("AUTOMEHANIČAR / KA", self.uv),
        )
        self.assertIn(
            "zavarivac-zavarivacica",
            shortage.match_shortage_occupations("TIG ZAVARIVAČ / ICA", self.uv),
        )

    def test_compound_title_requires_extra_words(self):
        truck = shortage.match_shortage_occupations(
            "VOZAČ / ICA TERETNOG VOZILA", self.uv
        )
        self.assertIn("vozac-vozacica-teretnog-vozila", truck)
        bus_only = shortage.match_shortage_occupations("VOZAČ AUTOBUSA", self.uv)
        self.assertIn("vozac-vozacica-autobusa", bus_only)
        self.assertNotIn("vozac-vozacica-teretnog-vozila", bus_only)

    def test_generic_radnik_does_not_match_construction_worker(self):
        hits = shortage.match_shortage_occupations("POMOĆNI RADNIK U SKLADIŠTU", self.uv)
        self.assertNotIn("radnik-radnica-visokogradnje", hits)
        self.assertNotIn("radnik-radnica-niskogradnje", hits)

    def test_apply_shortage_sets_listing_fields(self):
        listing = JobListing(
            web_sifra="1",
            title="KUHAR / ICA",
            employer="X",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url="https://example.test/1",
        )
        shortage.apply_shortage(listing, self.uv)
        self.assertTrue(listing.shortage_match)
        self.assertIn("kuhar-kuharica", listing.shortage_occupations)


class UvFreshnessTests(unittest.TestCase):
    def test_verified_date_within_stale_window(self):
        today = date(2026, 8, 21)
        age = shortage.verified_age_days(today=today)
        self.assertLessEqual(age, config.UV_STALE_AFTER_DAYS)
        shortage.assert_list_not_stale(today=today)

    def test_stale_verified_date_exits(self):
        with self.assertRaises(SystemExit):
            shortage.assert_list_not_stale(today=date(2027, 8, 21))


if __name__ == "__main__":
    unittest.main()
