from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import requests

import config
import notify
from main import should_publish_new_matches
from scraper import JobListing
from storage import StateStore


class PublishCadenceTests(unittest.TestCase):
    def test_new_listings_title_catch_up(self):
        from main import _new_listings_title

        tmp = tempfile.TemporaryDirectory()
        store = StateStore(Path(tmp.name) / "g.sqlite3")
        try:
            self.assertEqual(_new_listings_title(store), "New listings")
            store.mark_collect_success(date.today() - timedelta(days=3))
            self.assertIn("catch-up after 3 days", _new_listings_title(store))
        finally:
            store.close()
            tmp.cleanup()

    def test_smoke_prefix(self):
        with patch.object(config, "IS_SMOKE", True):
            from main import _telegram_label
            self.assertTrue(_telegram_label("New listings").startswith("SMOKE TEST"))

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
        self.assertNotIn("Shortage", line)
        self.assertIn("Acme", line)

    def test_shortage_tag_is_on_the_telegram_line(self):
        job = JobListing(
            web_sifra="1",
            title="KUHAR",
            employer="Acme",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url="https://example.test",
            deadline_date=date(2026, 12, 3),
            location_score=1,
            shortage_match=True,
        )
        line = notify.format_job_line(job)
        self.assertIn("Shortage · Zagreb", line)

    def test_job_line_translates_title_and_employer(self):
        job = JobListing(
            web_sifra="1",
            title="KUHAR / ICA",
            employer="Zdravstvena ustanova X",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url="https://example.test",
            deadline_date=date(2026, 9, 21),
            location_score=1,
        )

        def fake_translate(text: str) -> str | None:
            mapping = {
                "KUHAR / ICA": "Cook",
                "Zdravstvena ustanova X": "Health Institution X",
            }
            return mapping.get(text)

        line = notify.format_job_line(job, translate=fake_translate)
        self.assertIn("*Cook*", line)
        self.assertNotIn("KUHAR", line)
        self.assertIn("Zdravstvena ustanova X", line)
        self.assertIn("Health Institution X", line)
        employer_line = line.split("\n")[1]
        self.assertLess(employer_line.find("Zdravstvena ustanova X"), employer_line.find("Health Institution X"))

    def test_job_line_skips_employer_parens_when_translation_matches(self):
        job = JobListing(
            web_sifra="1",
            title="Developer",
            employer="Acme",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url="https://example.test",
            location_score=1,
        )
        line = notify.format_job_line(job, translate=lambda text: text)
        self.assertIn("Acme", line)
        self.assertNotIn("(", line.split("\n")[1])

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

    def test_verify_telegram_connection(self):
        def fake_get(url, params=None, timeout=20):
            class FakeResp:
                def json(self):
                    if url.endswith("getMe"):
                        return {"ok": True, "result": {"id": 1, "username": "jobs_bot"}}
                    return {"ok": True, "result": {"id": 111, "type": "private", "first_name": "Ada"}}
            return FakeResp()
        with patch("notify.requests.get", side_effect=fake_get):
            info = notify.verify_telegram_connection("tok", "111")
        self.assertEqual(info["bot_username"], "jobs_bot")
        self.assertEqual(info["chat_id"], 111)

    def test_send_retries_connection_reset_then_succeeds(self):
        calls = {"n": 0}

        class Ok:
            status_code = 200
            text = "ok"

            def raise_for_status(self):
                return None

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.ConnectionError("Connection reset by peer")
            return Ok()

        with patch("notify.requests.post", side_effect=fake_post), patch("notify.time.sleep") as sleep:
            notify._send_with_retry("token", "1", "hello", parse_mode=None)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleep.call_count, 2)

    def test_send_gives_up_after_repeated_connection_resets(self):
        with patch(
            "notify.requests.post",
            side_effect=requests.ConnectionError("Connection reset by peer"),
        ), patch("notify.time.sleep") as sleep:
            with self.assertRaises(requests.ConnectionError):
                notify._send_with_retry("token", "1", "hello", max_attempts=3, parse_mode=None)
        self.assertEqual(sleep.call_count, 2)

    def test_send_retries_server_error_then_succeeds(self):
        calls = {"n": 0}

        class Resp:
            def __init__(self, status):
                self.status_code = status
                self.text = "upstream" if status != 200 else "ok"

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError(str(self.status_code))

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            return Resp(502 if calls["n"] == 1 else 200)

        with patch("notify.requests.post", side_effect=fake_post), patch("notify.time.sleep") as sleep:
            notify._send_with_retry("token", "1", "hello", parse_mode=None)
        self.assertEqual(calls["n"], 2)
        sleep.assert_called_once_with(1)

    def test_send_does_not_retry_client_errors(self):
        class Bad:
            status_code = 400
            text = "Bad Request: can't parse entities"

            def raise_for_status(self):
                raise requests.HTTPError("400")

        with patch("notify.requests.post", return_value=Bad()) as post, patch("notify.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                notify._send_with_retry("token", "1", "hello", parse_mode=None)
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()


class ShortageDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "test.sqlite3")
        self.store.set_meta(config.TELEGRAM_SHORTAGE_INCLUDE_AFTER_KEY, "2026-11-17T05:00:00")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _store_shortage(self, sifra: str, deadline: date, seen: str) -> None:
        job = JobListing(
            web_sifra=sifra,
            title=f"Job {sifra}",
            employer="Acme",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url=f"https://example.test/{sifra}",
            deadline_date=deadline,
            foreign_score=0,
            location_score=1,
            shortage_match=True,
        )
        self.store.upsert_job(job, digest_day=None)
        self.store._conn.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE web_sifra = ?",
            (seen, sifra),
        )
        self.store._conn.commit()

    def test_dispatch_sends_long_deadline_backfill_and_skips_zero_notice(self):
        from main import _dispatch_telegram

        self._store_shortage("long", date(2026, 12, 3), "2026-11-10T10:00:00")
        self._store_shortage("soon", date(2026, 11, 20), "2026-11-10T10:00:00")
        digests = []
        news = []

        with patch("main.notify.send_telegram_digest", side_effect=lambda *a, **k: digests.append(a)):
            with patch("main.notify.send_new_matches_report", side_effect=lambda *a, **k: news.append(a)):
                _dispatch_telegram(self.store, "tok", "1")

        self.assertEqual(len(digests), 1)
        self.assertEqual(digests[0][2], "Shortage listings")
        self.assertEqual([row["web_sifra"] for row in digests[0][3]], ["long"])
        self.assertEqual(news, [])
        notified = {
            row["web_sifra"]: row["notified_at"]
            for row in self.store._conn.execute("SELECT web_sifra, notified_at FROM jobs")
        }
        self.assertIsNotNone(notified["long"])
        self.assertIsNone(notified["soon"])


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

    def _seen(self, sifra: str, stamp: str) -> None:
        self.store._conn.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE web_sifra = ?",
            (stamp, sifra),
        )
        self.store._conn.commit()

    def test_new_shortage_jobs_in_zagreb_are_telegram_matches(self):
        self.store.set_meta(config.TELEGRAM_SHORTAGE_INCLUDE_AFTER_KEY, "2026-11-17T00:00:00")
        keyword = self._listing("kw")
        keyword.deadline_date = date(2026, 11, 25)
        shortage_only = self._listing("uv")
        shortage_only.foreign_score = 0
        shortage_only.shortage_match = True
        shortage_only.shortage_occupations = ["kuhar-kuharica"]
        shortage_only.deadline_date = date(2026, 11, 28)
        outer = self._listing("sesvete")
        outer.foreign_score = 0
        outer.location_raw = "SESVETE"
        outer.shortage_match = True
        soon = self._listing("soon")
        soon.foreign_score = 0
        soon.shortage_match = True
        soon.deadline_date = date(2026, 11, 20)
        self.store.upsert_job(keyword, digest_day=None)
        self.store.upsert_job(shortage_only, digest_day=None)
        self.store.upsert_job(outer, digest_day=None)
        self.store.upsert_job(soon, digest_day=None)
        for sifra in ("kw", "uv", "sesvete", "soon"):
            self._seen(sifra, "2026-11-17T06:00:00")
        new = [row["web_sifra"] for row in self.store.unnotified_new_matches()]
        self.assertEqual(new, ["soon", "kw", "uv"])

    def test_outer_settlement_keyword_ads_stay_out_of_telegram(self):
        dubrava = self._listing("dubrava")
        dubrava.location_raw = "ZAGREB-DUBRAVA"
        self.store.upsert_job(dubrava, digest_day=None)
        self.assertEqual(list(self.store.unnotified_new_matches()), [])

    def test_shortage_backfill_is_zagreb_and_deadline_after_30_nov(self):
        cutoff = "2026-11-17T05:00:00"
        self.store.set_meta(config.TELEGRAM_SHORTAGE_INCLUDE_AFTER_KEY, cutoff)

        def shortage(sifra: str, location: str, deadline: date | None) -> None:
            job = self._listing(sifra)
            job.foreign_score = 0
            job.shortage_match = True
            job.location_raw = location
            job.deadline_date = deadline
            self.store.upsert_job(job, digest_day=None)
            self._seen(sifra, "2026-11-10T10:00:00")

        shortage("long", "ZAGREB", date(2026, 12, 1))
        shortage("exact", "ZAGREB", date(2026, 11, 30))
        shortage("soon", "ZAGREB", date(2026, 11, 20))
        shortage("sesvete", "SESVETE", date(2026, 12, 6))
        shortage("novi", "ZAGREB-NOVI ZAGREB", date(2026, 12, 6))
        fresh = self._listing("fresh")
        fresh.foreign_score = 0
        fresh.shortage_match = True
        fresh.deadline_date = date(2026, 12, 10)
        self.store.upsert_job(fresh, digest_day=None)
        self._seen("fresh", "2026-11-17T06:00:00")
        old_keyword = self._listing("oldkw")
        old_keyword.deadline_date = date(2026, 12, 2)
        self.store.upsert_job(old_keyword, digest_day=None)
        self._seen("oldkw", "2026-11-10T10:00:00")

        backfill = [row["web_sifra"] for row in self.store.shortage_backfill_matches(
            config.SHORTAGE_BACKFILL_DEADLINE_AFTER, cutoff
        )]
        self.assertEqual(backfill, ["long"])
        new = [row["web_sifra"] for row in self.store.unnotified_new_matches()]
        self.assertEqual(new, ["oldkw", "fresh"])

    def test_collect_gap_days(self):
        self.assertIsNone(self.store.days_since_last_success(date(2026, 8, 21)))
        self.store.mark_collect_success(date(2026, 8, 19))
        self.assertEqual(self.store.days_since_last_success(date(2026, 8, 21)), 2)
        self.assertEqual(self.store.days_since_last_success(date(2026, 8, 19)), 0)

    def test_prune_keeps_jobs_for_three_days_after_deadline(self):
        today = date(2026, 8, 21)
        keep_open = self._listing("open")
        keep_open.deadline_date = None
        keep_recent = self._listing("expired-2d")
        keep_recent.deadline_date = date(2026, 8, 19)  # 2 days ago
        drop_on_day_3 = self._listing("expired-3d")
        drop_on_day_3.deadline_date = date(2026, 8, 18)
        drop_older = self._listing("expired-4d")
        drop_older.deadline_date = date(2026, 8, 17)
        for job in (keep_open, keep_recent, drop_on_day_3, drop_older):
            self.store.upsert_job(job)

        still_listed = self._listing("inspected-keep")
        still_listed.deadline_raw = "19.8.2026."
        stale = self._listing("inspected-drop")
        stale.deadline_raw = "18.8.2026."
        self.store.record_listing(still_listed)
        self.store.record_listing(stale)

        removed = self.store.prune_expired(before=today)
        self.assertEqual(removed, 3)  # 2 jobs + 1 inspected
        remaining_jobs = {
            row["web_sifra"]
            for row in self.store._conn.execute("SELECT web_sifra FROM jobs")
        }
        self.assertEqual(remaining_jobs, {"open", "expired-2d"})
        remaining_inspected = {
            row["web_sifra"]
            for row in self.store._conn.execute("SELECT web_sifra FROM inspected")
        }
        self.assertEqual(remaining_inspected, {"inspected-keep"})

    def test_prune_drops_open_ended_after_three_months(self):
        today = date(2026, 8, 21)
        keep = self._listing("open-new")
        keep.deadline_date = None
        drop = self._listing("open-old")
        drop.deadline_date = None
        self.store.upsert_job(keep)
        self.store.upsert_job(drop)
        self.store._conn.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE web_sifra = ?",
            ("2026-05-23T00:00:00", "open-old"),  # 90 days before today
        )
        self.store._conn.commit()

        keep_listed = self._listing("insp-open-new")
        keep_listed.deadline_raw = ""
        drop_listed = self._listing("insp-open-old")
        drop_listed.deadline_raw = ""
        self.store.record_listing(keep_listed)
        self.store.record_listing(drop_listed)
        self.store._conn.execute(
            "UPDATE inspected SET listed_at = ? WHERE web_sifra = ?",
            ("2026-05-23T00:00:00", "insp-open-old"),
        )
        self.store._conn.commit()

        removed = self.store.prune_expired(before=today)
        self.assertEqual(removed, 2)
        jobs = {
            row["web_sifra"]
            for row in self.store._conn.execute("SELECT web_sifra FROM jobs")
        }
        self.assertEqual(jobs, {"open-new"})
        inspected = {
            row["web_sifra"]
            for row in self.store._conn.execute("SELECT web_sifra FROM inspected")
        }
        self.assertEqual(inspected, {"insp-open-new"})


if __name__ == "__main__":
    unittest.main()
