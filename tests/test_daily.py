from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import config
import notify
from main import should_publish_new_matches
from scraper import JobListing
from storage import StateStore


class PublishCadenceTests(unittest.TestCase):
    def test_daily_always_publishes(self):
        with patch.object(config, "NEW_MATCH_PUBLISH_CADENCE", "daily"):
            self.assertTrue(should_publish_new_matches(date(2026, 8, 21)))  # Friday
            self.assertTrue(should_publish_new_matches(date(2026, 8, 24)))  # Monday

    def test_weekly_only_on_configured_weekday(self):
        monday = date(2026, 8, 24)
        friday = date(2026, 8, 21)
        with patch.object(config, "NEW_MATCH_PUBLISH_CADENCE", "weekly"):
            with patch.object(config, "NEW_MATCH_PUBLISH_WEEKDAY", 0):
                self.assertTrue(should_publish_new_matches(monday))
                self.assertFalse(should_publish_new_matches(friday))


class NotifyTests(unittest.TestCase):
    def test_zero_notice_is_non_empty_markdown(self):
        text = notify.build_zero_new_matches_message()
        self.assertIn("New listings", text)
        self.assertIn("No new listings matched the filter today", text)

    def test_as_job_dict_from_listing(self):
        job = JobListing(
            web_sifra="1",
            title="Test",
            employer="Acme",
            location_raw="ZAGREB",
            deadline_raw="21.9.2026.",
            detail_url="https://example.test",
            deadline_date=date(2026, 9, 21),
            location_score=2,
        )
        d = notify.as_job_dict(job)
        self.assertEqual(d["title"], "Test")
        self.assertEqual(d["deadline_date"], "2026-09-21")
        line = notify.format_job_line(job)
        self.assertIn("Test", line)
        self.assertIn("City centre", line)

    def test_fetch_chat_ids_dedupes(self):
        payload = {
            "ok": True,
            "result": [
                {"message": {"chat": {"id": 111, "type": "private", "first_name": "Ada"}}},
                {"message": {"chat": {"id": 111, "type": "private", "first_name": "Ada"}}},
                {"channel_post": {"chat": {"id": -100222, "type": "channel", "title": "Jobs"}}},
            ],
        }
        class FakeResp:
            def raise_for_status(self):
                return None
            def json(self):
                return payload
        with patch("notify.requests.get", return_value=FakeResp()):
            chats = notify.fetch_chat_ids("token")
        ids = {c["id"] for c in chats}
        self.assertEqual(ids, {111, -100222})

    def test_fetch_chat_ids_from_start_membership(self):
        payload = {
            "ok": True,
            "result": [
                {"my_chat_member": {"chat": {"id": 999, "type": "private", "first_name": "Ada"}}},
            ],
        }
        class FakeResp:
            def raise_for_status(self):
                return None
            def json(self):
                return payload
        with patch("notify.requests.get", return_value=FakeResp()):
            chats = notify.fetch_chat_ids("token")
        self.assertEqual(chats[0]["id"], 999)


class StorageNewMatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _listing(self, sifra: str) -> JobListing:
        return JobListing(
            web_sifra=sifra,
            title=f"Job {sifra}",
            employer="Acme",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url=f"https://example.test/{sifra}",
            deadline_date=date.today() + timedelta(days=10),
            foreign_score=3,
            location_score=1,
        )

    def test_empty_and_backlog_vs_new(self):
        self.assertTrue(self.store.is_empty())
        self.store.upsert_job(self._listing("backlog"), digest_day=1)
        self.store.upsert_job(self._listing("fresh"), digest_day=None)
        self.assertFalse(self.store.is_empty())
        new = self.store.unnotified_new_matches()
        self.assertEqual([row["web_sifra"] for row in new], ["fresh"])


if __name__ == "__main__":
    unittest.main()
