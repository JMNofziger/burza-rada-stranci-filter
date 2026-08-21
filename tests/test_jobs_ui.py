from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from scraper import JobListing
from storage import StateStore, job_row_to_view, location_label_for, urgency_for_days
from web.export import jobs_payload, write_jobs_json


TODAY = date(2026, 8, 21)


class UrgencyHelperTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(urgency_for_days(None), "open")
        self.assertEqual(urgency_for_days(-1), "expired")
        self.assertEqual(urgency_for_days(0), "48h")
        self.assertEqual(urgency_for_days(2), "48h")
        self.assertEqual(urgency_for_days(3), "7d")
        self.assertEqual(urgency_for_days(7), "7d")
        self.assertEqual(urgency_for_days(8), "later")

    def test_location_label(self):
        self.assertEqual(location_label_for(2), "City centre")
        self.assertEqual(location_label_for(1), "Zagreb")
        self.assertEqual(location_label_for(0), "Zagreb")


class ListJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "jobs.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _listing(
        self,
        sifra: str,
        *,
        deadline: date | None,
        location_score: int = 1,
        keywords: list[str] | None = None,
    ) -> JobListing:
        return JobListing(
            web_sifra=sifra,
            title=f"Job {sifra}",
            employer="Acme",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url=f"https://example.test/{sifra}",
            deadline_date=deadline,
            foreign_score=3,
            location_score=location_score,
            matched_keywords=keywords or ["radna dozvola"],
        )

    def test_empty_database(self):
        self.assertEqual(self.store.list_jobs(today=TODAY), [])

    def test_computed_fields_and_sort(self):
        self.store.upsert_job(self._listing("open", deadline=None), digest_day=None)
        self.store.upsert_job(
            self._listing("soon", deadline=TODAY + timedelta(days=1), location_score=2),
            digest_day=2,
        )
        self.store.upsert_job(
            self._listing("week", deadline=TODAY + timedelta(days=6)),
            digest_day=None,
        )
        self.store.upsert_job(
            self._listing("later", deadline=TODAY + timedelta(days=30)),
            digest_day=None,
        )
        self.store.upsert_job(
            self._listing("expired", deadline=TODAY - timedelta(days=1)),
            digest_day=None,
        )
        self.store.mark_notified("soon")

        rows = {job["web_sifra"]: job for job in self.store.list_jobs(today=TODAY)}
        self.assertEqual(set(rows), {"open", "soon", "week", "later", "expired"})

        self.assertIsNone(rows["open"]["deadline_date"])
        self.assertIsNone(rows["open"]["days_until_deadline"])
        self.assertEqual(rows["open"]["urgency"], "open")
        self.assertFalse(rows["open"]["notified"])

        self.assertEqual(rows["soon"]["deadline_date"], "2026-08-22")
        self.assertEqual(rows["soon"]["days_until_deadline"], 1)
        self.assertEqual(rows["soon"]["urgency"], "48h")
        self.assertEqual(rows["soon"]["location_label"], "City centre")
        self.assertTrue(rows["soon"]["notified"])
        self.assertEqual(rows["soon"]["digest_day"], 2)
        self.assertEqual(rows["soon"]["matched_keywords"], "radna dozvola")

        self.assertEqual(rows["week"]["days_until_deadline"], 6)
        self.assertEqual(rows["week"]["urgency"], "7d")
        self.assertEqual(rows["week"]["location_label"], "Zagreb")

        self.assertEqual(rows["later"]["urgency"], "later")
        self.assertEqual(rows["expired"]["urgency"], "expired")
        self.assertEqual(rows["expired"]["days_until_deadline"], -1)

        ordered = [job["web_sifra"] for job in self.store.list_jobs(today=TODAY)]
        self.assertEqual(ordered, ["expired", "soon", "week", "later", "open"])

    def test_inspected_rows_are_not_listed(self):
        inspected = self._listing("skip-me", deadline=TODAY + timedelta(days=4))
        self.store.record_listing(inspected)
        self.store.upsert_job(self._listing("keep-me", deadline=TODAY + timedelta(days=4)))
        jobs = self.store.list_jobs(today=TODAY)
        self.assertEqual([job["web_sifra"] for job in jobs], ["keep-me"])

    def test_row_view_null_deadline(self):
        view = job_row_to_view(
            {
                "web_sifra": "x",
                "title": "T",
                "employer": None,
                "location_raw": None,
                "deadline_date": None,
                "foreign_score": 2,
                "location_score": 2,
                "matched_keywords": None,
                "detail_url": "https://example.test/x",
                "first_seen_at": "2026-08-21T00:00:00",
                "digest_day": None,
                "notified_at": None,
            },
            today=TODAY,
        )
        self.assertEqual(view["urgency"], "open")
        self.assertEqual(view["location_label"], "City centre")
        self.assertFalse(view["notified"])


class ExportWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "jobs.sqlite3"
        self.store = StateStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_empty_export(self):
        dest = Path(self.tmp.name) / "jobs.json"
        write_jobs_json(self.store, path=dest, now=datetime(2026, 8, 21, 9, 0, 0), today=TODAY)
        payload = json.loads(dest.read_text())
        self.assertEqual(payload["jobs"], [])
        self.assertEqual(payload["generated_at"], "2026-08-21T09:00:00Z")

    def test_seeded_export_and_payload(self):
        self.store.upsert_job(
            JobListing(
                web_sifra="165734230",
                title="INZENJER",
                employer="Dominus",
                location_raw="ZAGREB",
                deadline_raw="",
                detail_url="https://example.test/165734230",
                deadline_date=TODAY + timedelta(days=2),
                foreign_score=3,
                location_score=2,
                matched_keywords=["radnu dozvolu"],
            )
        )
        dest = Path(self.tmp.name) / "jobs.json"
        write_jobs_json(self.store, path=dest, now=datetime(2026, 8, 21, 9, 0, 0), today=TODAY)
        payload = json.loads(dest.read_text())
        self.assertEqual(len(payload["jobs"]), 1)
        job = payload["jobs"][0]
        self.assertEqual(job["web_sifra"], "165734230")
        self.assertEqual(job["urgency"], "48h")
        self.assertEqual(job["location_label"], "City centre")
        self.assertNotIn("TELEGRAM_BOT_TOKEN", dest.read_text())
        helper = jobs_payload(self.store, now=datetime(2026, 8, 21, 9, 0, 0), today=TODAY)
        self.assertEqual(helper["jobs"][0]["days_until_deadline"], 2)


class PublicBoardStaticTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.root = root
        self.html = (root / "docs" / "index.html").read_text()
        self.css = (root / "docs" / "styles.css").read_text()
        self.js = (root / "docs" / "app.js").read_text()
        self.method = (root / "docs" / "method.html").read_text()

    def test_board_is_a_filter_drawer_not_a_local_server(self):
        self.assertIn('id="filters"', self.html)
        self.assertIn("Expiring", self.html)
        self.assertIn("./jobs.json", self.js)
        self.assertIn(".drawer", self.css)
        self.assertNotIn("tabulator", self.html.lower())
        self.assertNotIn("127.0.0.1", self.html)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", self.html + self.js)
        self.assertNotIn("TELEGRAM_CHAT_ID", self.html + self.js)

    def test_empty_checkbox_filters_mean_show_all(self):
        self.assertIn("if (urgency.size && !urgency.has(job.urgency))", self.js)
        self.assertIn("if (location.size && !location.has(job.location_label))", self.js)
        self.assertIn("if (!on.length || on.length === boxes.length)", self.js)
        self.assertNotIn('name="urgency" value="48h" checked', self.html)
        self.assertNotIn('name="location" value="City centre" checked', self.html)

    def test_listing_count_sits_above_results(self):
        main = self.html.split("<main>", 1)[1].split("</main>", 1)[0]
        self.assertLess(main.find('id="listing-count"'), main.find('id="results"'))
        self.assertNotIn('id="row-count"', self.html)

    def test_nav_has_language_and_dark_default_theme_toggle(self):
        self.assertIn('data-theme="dark"', self.html)
        self.assertIn('id="lang-en"', self.html)
        self.assertIn('id="lang-hr"', self.html)
        self.assertIn('id="theme-toggle"', self.html)
        self.assertIn("Fraunces", self.html)
        self.assertIn("Sora", self.html)
        self.assertIn("--accent: #e0a14a", self.css)
        self.assertIn("title_en", self.js)
        self.assertIn("langpair=hr|en", self.js)

    def test_search_and_tools_live_in_main_not_header(self):
        header = self.html.split("<header", 1)[1].split("</header>", 1)[0]
        main = self.html.split("<main>", 1)[1].split("</main>", 1)[0]
        self.assertNotIn('id="search"', header)
        self.assertNotIn('id="sort"', header)
        self.assertNotIn('id="listing-count"', header)
        self.assertIn('id="search"', main)
        self.assertIn('id="sort"', main)
        self.assertIn('id="listing-count"', main)
        self.assertIn('class="board"', self.html)

    def test_desktop_board_grid_keeps_drawer_below_header(self):
        self.assertIn(".board {", self.css)
        self.assertIn("grid-template-columns: 260px minmax(0, 1fr)", self.css)
        self.assertNotRegex(self.css, r"body\s*\{[^}]*grid-template-columns:\s*260px")
        topbar_z = int(re.search(r"\.topbar\s*\{[^}]*z-index:\s*(\d+)", self.css).group(1))
        self.assertGreaterEqual(topbar_z, 50)
        desktop = self.css.split("@media (min-width: 860px)", 1)[1]
        self.assertIn("z-index: 1", desktop)
        self.assertIn("position: sticky", desktop)
        self.assertIn("top: 5.25rem", desktop)
        self.assertIn("align-self: start", desktop)
        self.assertGreater(topbar_z, 1)

    def test_tooltips_on_score_keywords_and_urgency(self):
        self.assertIn("data-tip", self.js)
        self.assertIn("has-tip", self.js)
        self.assertIn("tipScore", self.js)
        self.assertIn("tipKeyword", self.js)
        self.assertIn("tipUrgency48h", self.js)
        self.assertIn("tipLocCentre", self.js)
        self.assertIn("tipTelegram", self.js)
        self.assertIn('.has-tip', self.css)
        self.assertIn("content: attr(data-tip)", self.css)
        self.assertIn('.split(",")', self.js)

    def test_method_page_linked_and_bilingual(self):
        self.assertTrue((self.root / "docs" / "method.html").is_file())
        self.assertIn('href="./method.html"', self.html)
        self.assertIn('data-i18n="method"', self.html)
        self.assertIn('method: "Method"', self.js)
        self.assertIn('href="./index.html"', self.method)
        self.assertIn('data-lang-panel="en"', self.method)
        self.assertIn('data-lang-panel="hr"', self.method)
        self.assertIn("WebSifra", self.method)
        self.assertIn("FOREIGN_SCORE_THRESHOLD", self.method)
        self.assertIn("Grad Zagreb", self.method)
        self.assertIn("Svi poslovi", self.method)
        self.assertNotIn('id="results"', self.method)
        self.assertNotIn('id="search"', self.method)
        self.assertIn("IS_BOARD", self.js)
        self.assertIn('Boolean($("results"))', self.js)

    def test_method_page_documents_foreigner_score_lexicon(self):
        import config

        self.assertIn("Weight 3", self.method)
        self.assertIn("Weight 2", self.method)
        self.assertIn("Weight 1", self.method)
        self.assertIn("Težina 3", self.method)
        self.assertIn("Težina 2", self.method)
        self.assertIn("Težina 1", self.method)
        self.assertIn("radna dozvola", self.method)
        self.assertIn("strani državljani", self.method)
        self.assertIn("NEGATION_WINDOW_CHARS = 25", self.method)
        self.assertIn("FOREIGN_SCORE_THRESHOLD = 2", self.method)
        self.assertIn("isključivo eu", self.method)
        self.assertIn("samo eu državljani", self.method)
        for phrase in config.FOREIGNER_KEYWORDS:
            self.assertIn(f"<code>{phrase}</code>", self.method)
        for marker in config.NEGATION_MARKERS:
            self.assertIn(f"<code>{marker}</code>", self.method)
        self.assertGreaterEqual(self.method.count("radna dozvola"), 2)
        self.assertGreaterEqual(self.method.count("strani državljani"), 2)


if __name__ == "__main__":
    unittest.main()
