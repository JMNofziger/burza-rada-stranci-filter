from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scraper import JobListing
from storage import StateStore


def _listing(sifra: str, category: str = "IT") -> JobListing:
    return JobListing(
        web_sifra=sifra,
        title=f"Job {sifra}",
        employer="Acme",
        location_raw="ZAGREB",
        deadline_raw="21.9.2026.",
        detail_url=f"https://example.test/{sifra}",
        deadline_date=date(2026, 9, 21),
        category_label=category,
        foreign_score=3,
        location_score=1,
        description="dozvola za boravak i rad",
    )


class InspectedCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_record_listing_and_pending(self):
        self.store.record_listing(_listing("1"))
        self.store.record_listing(_listing("2"))
        self.assertEqual(self.store.count_inspected(), 2)
        self.assertEqual(self.store.count_pending_details(), 2)
        self.assertFalse(self.store.is_detail_fetched("1"))

        self.store.mark_detail_fetched("1", matched=False, skip_reason="no_keywords")
        self.assertTrue(self.store.is_detail_fetched("1"))
        pending = self.store.pending_inspected()
        self.assertEqual([row["web_sifra"] for row in pending], ["2"])

    def test_record_listing_does_not_reset_fetched(self):
        self.store.record_listing(_listing("1"))
        self.store.mark_detail_fetched("1", matched=True)
        self.store.record_listing(_listing("1", category="Health"))
        self.assertTrue(self.store.is_detail_fetched("1"))
        self.assertEqual(self.store.count_pending_details(), 0)

    def test_pending_limit(self):
        for i in range(5):
            self.store.record_listing(_listing(str(i)))
        batch = self.store.pending_inspected(limit=2)
        self.assertEqual(len(batch), 2)
        self.assertEqual([row["web_sifra"] for row in batch], ["0", "1"])

    def test_category_complete_is_per_run(self):
        run_a = self.store.start_scrape_run()
        run_b = self.store.start_scrape_run()
        target = "ctl00$MainContent$lnkKategorija$0"
        self.store.mark_category_complete(run_a, target, "IT 12")
        self.assertTrue(self.store.is_category_complete(run_a, target))
        self.assertFalse(self.store.is_category_complete(run_b, target))
        self.assertEqual(self.store.count_completed_categories(run_a), 1)
        self.assertEqual(self.store.count_completed_categories(run_b), 0)


class FullScrapeStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_suggests_list_then_details_then_notify(self):
        from main import full_scrape_status_dict, run_full_scrape_status

        store = StateStore(self.db)
        try:
            status = full_scrape_status_dict(store)
            self.assertEqual(status["suggested_phase"], "list")
            self.assertIsNone(status["open_run_id"])

            run_id = store.start_scrape_run()
            store.record_listing(_listing("1"))
            store.record_listing(_listing("2"))
            store.mark_run_list_complete(run_id)
            status = full_scrape_status_dict(store)
            self.assertEqual(status["suggested_phase"], "details")
            self.assertEqual(status["details_pending"], 2)
            self.assertTrue(status["list_complete"])

            store.mark_detail_fetched("1", matched=True)
            store.mark_detail_fetched("2", matched=False)
            store.upsert_job(_listing("1"), digest_day=None)
            store.mark_run_details_complete(run_id)
            status = full_scrape_status_dict(store)
            self.assertEqual(status["suggested_phase"], "notify")
            self.assertEqual(status["unnotified"], 1)

            store.mark_notified("1")
            store.mark_run_notify_complete(run_id)
            status = full_scrape_status_dict(store)
            self.assertEqual(status["suggested_phase"], "done")
        finally:
            store.close()

        with patch("main.StateStore", lambda: StateStore(self.db)):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                run_full_scrape_status()
            parsed = json.loads(buf.getvalue())
            self.assertEqual(parsed["suggested_phase"], "done")


class CollectSkipsFetchedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_collect_skips_already_fetched_non_matches(self):
        from main import collect_and_score

        seen = _listing("seen-nonmatch")
        fresh = _listing("fresh-match")
        self.store.record_listing(seen)
        self.store.mark_detail_fetched(seen.web_sifra, matched=False)

        def fake_fetch(session, listing):
            listing.description = "dozvola za boravak i rad"
            return listing

        with patch("main.iter_zagreb_candidates", return_value=[seen, fresh]):
            with patch("main.fetch_detail", side_effect=fake_fetch) as fetch:
                results = collect_and_score(None, self.store, skip_seen=True)
        self.assertEqual([job.web_sifra for job in results], ["fresh-match"])
        fetch.assert_called_once()
        self.assertTrue(self.store.is_detail_fetched("fresh-match"))
        self.assertTrue(self.store.is_detail_fetched("seen-nonmatch"))


class DetailsBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"
        self.store = StateStore(self.db)
        for i in range(3):
            self.store.record_listing(_listing(str(i)))
        self.store.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_details_honors_limit_and_upserts_matches(self):
        from main import run_full_scrape_details

        def fake_fetch(session, listing):
            listing.description = "dozvola za boravak i rad"
            return listing

        with patch("main.StateStore", lambda: StateStore(self.db)):
            with patch("main.build_session", return_value=None):
                with patch("main.fetch_detail", side_effect=fake_fetch):
                    n = run_full_scrape_details(limit=2)
        self.assertEqual(n, 2)
        store = StateStore(self.db)
        try:
            self.assertEqual(store.count_pending_details(), 1)
            self.assertEqual(store.count_jobs(), 2)
        finally:
            store.close()

    def test_failed_detail_stays_pending(self):
        from main import run_full_scrape_details

        with patch("main.StateStore", lambda: StateStore(self.db)):
            with patch("main.build_session", return_value=None):
                with patch("main.fetch_detail", return_value=None):
                    n = run_full_scrape_details(limit=1)
        self.assertEqual(n, 0)
        store = StateStore(self.db)
        try:
            self.assertEqual(store.count_pending_details(), 3)
        finally:
            store.close()


class FirstFillNotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"
        store = StateStore(self.db)
        listing = _listing("99")
        store.upsert_job(listing, digest_day=None)
        store.start_scrape_run()
        store.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_fill_seeds_backlog_instead_of_flooding(self):
        from main import run_full_scrape_notify

        sent = []

        def fake_new(token, chat_id, rows, title=""):
            sent.append(("new", list(rows), title))

        def fake_digest(token, chat_id, title, rows):
            sent.append(("digest", title, [r["web_sifra"] for r in rows]))

        with patch("main.StateStore", lambda: StateStore(self.db)):
            with patch("main.get_telegram_creds", return_value=("tok", "111")):
                with patch("main.notify.send_new_matches_report", side_effect=fake_new):
                    with patch("main.notify.send_telegram_digest", side_effect=fake_digest):
                        run_full_scrape_notify()

        store = StateStore(self.db)
        try:
            self.assertIsNotNone(store.get_meta("last_successful_collect_on"))
            self.assertIsNotNone(store.get_meta("last_full_scrape_on"))
            row = store._conn.execute("SELECT digest_day FROM jobs WHERE web_sifra='99'").fetchone()
            self.assertEqual(row["digest_day"], 1)
            new_calls = [c for c in sent if c[0] == "new"]
            self.assertEqual(len(new_calls[0][1]), 0)
        finally:
            store.close()


class ResolveRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_resumes_open_run_and_starts_new_after_notify(self):
        from main import resolve_scrape_run

        first = resolve_scrape_run(self.store)
        again = resolve_scrape_run(self.store)
        self.assertEqual(first, again)
        self.store.mark_run_notify_complete(first)
        third = resolve_scrape_run(self.store)
        self.assertNotEqual(third, first)
        reset = resolve_scrape_run(self.store, reset_list=True)
        self.assertNotEqual(reset, third)


class ListResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_skips_completed_categories_and_records(self):
        from main import run_full_scrape_list

        store = StateStore(self.db)
        run_id = store.start_scrape_run()
        store.mark_category_complete(run_id, "ctl00$done", "Done 1")
        store.close()

        skip_seen = []
        completed = []

        def fake_iter(session, skip_category=None, on_category_complete=None):
            skip_seen.append(skip_category("ctl00$done", "Done 1"))
            skip_seen.append(skip_category("ctl00$todo", "Todo 2"))
            listing = _listing("42", category="Todo 2")
            yield listing
            on_category_complete("ctl00$todo", "Todo 2")
            completed.append("ctl00$todo")

        with patch("main.StateStore", lambda: StateStore(self.db)):
            with patch("main.build_session", return_value=None):
                with patch("main.iter_zagreb_candidates", side_effect=fake_iter):
                    run_full_scrape_list()

        store = StateStore(self.db)
        try:
            self.assertEqual(skip_seen, [True, False])
            self.assertTrue(store.is_category_complete(run_id, "ctl00$todo"))
            self.assertEqual(store.count_inspected(), 1)
            self.assertTrue(store.get_scrape_run(run_id)["list_completed_at"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
