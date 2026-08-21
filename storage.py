"""
storage.py
SQLite-backed state store.

Why not seen_jobs.json
------------------------
A single flat JSON file has three real problems at this pipeline's scale
(thousands of rows, growing daily, cron-driven):
  1. No atomicity -- a crash or overlapping cron run mid-write can truncate
     or corrupt the file, silently wiping dedup history.
  2. No indexing -- "which seen jobs are still open and closing within 48h"
     requires loading and scanning the entire file every run.
  3. No schema -- nothing stops a future field rename from silently breaking
     old entries.

SQLite (stdlib, zero extra infra, WAL mode for crash-safety) fixes all three
while still being a single portable file, which matters if you deploy this
via GitHub Actions and commit the DB file back to the repo (see README).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    web_sifra       TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    employer        TEXT,
    location_raw    TEXT,
    deadline_date   TEXT,              -- ISO date, NULL = open-ended
    foreign_score   INTEGER NOT NULL,
    location_score  INTEGER NOT NULL,
    matched_keywords TEXT,             -- comma-joined, for debugging/audit
    detail_url      TEXT,
    first_seen_at   TEXT NOT NULL,     -- ISO datetime
    digest_day      INTEGER,           -- 1..6 for the initial bootstrap, else NULL
    notified_at     TEXT               -- ISO datetime, NULL until dispatched
);
CREATE INDEX IF NOT EXISTS idx_jobs_deadline ON jobs (deadline_date);
CREATE INDEX IF NOT EXISTS idx_jobs_notified ON jobs (notified_at);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspected (
    web_sifra         TEXT PRIMARY KEY,
    title             TEXT,
    employer          TEXT,
    location_raw      TEXT,
    deadline_raw      TEXT,
    detail_url        TEXT,
    category_label    TEXT,
    listed_at         TEXT NOT NULL,
    detail_fetched    INTEGER NOT NULL DEFAULT 0,
    detail_fetched_at TEXT,
    matched           INTEGER,
    skip_reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_inspected_pending
    ON inspected (detail_fetched, listed_at, web_sifra);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at           TEXT NOT NULL,
    list_completed_at    TEXT,
    details_completed_at TEXT,
    notify_completed_at  TEXT
);
CREATE TABLE IF NOT EXISTS scrape_categories (
    run_id        INTEGER NOT NULL,
    event_target  TEXT NOT NULL,
    label         TEXT,
    completed_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, event_target),
    FOREIGN KEY (run_id) REFERENCES scrape_runs(id)
);
"""


class StateStore:
    def __init__(self, db_path: Path = config.DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            self._conn.commit()
        except sqlite3.Error:
            pass
        self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def is_seen(self, web_sifra: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM jobs WHERE web_sifra = ? LIMIT 1", (web_sifra,)
        )
        return cur.fetchone() is not None

    def is_empty(self) -> bool:
        cur = self._conn.execute("SELECT 1 FROM jobs LIMIT 1")
        return cur.fetchone() is None

    def unnotified_new_matches(self) -> list[sqlite3.Row]:
        """Jobs discovered after bootstrap (digest_day IS NULL) and not yet published."""
        cur = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE notified_at IS NULL
              AND digest_day IS NULL
            ORDER BY deadline_date IS NULL, deadline_date
            """
        )
        return cur.fetchall()

    @contextmanager
    def transaction(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def upsert_job(self, listing, digest_day: int | None = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    web_sifra, title, employer, location_raw, deadline_date,
                    foreign_score, location_score, matched_keywords,
                    detail_url, first_seen_at, digest_day
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(web_sifra) DO UPDATE SET
                    deadline_date=excluded.deadline_date,
                    foreign_score=excluded.foreign_score,
                    location_score=excluded.location_score,
                    matched_keywords=excluded.matched_keywords,
                    digest_day=COALESCE(excluded.digest_day, jobs.digest_day)
                """,
                (
                    listing.web_sifra,
                    listing.title,
                    listing.employer,
                    listing.location_raw,
                    listing.deadline_date.isoformat() if listing.deadline_date else None,
                    listing.foreign_score,
                    listing.location_score,
                    ",".join(listing.matched_keywords),
                    listing.detail_url,
                    datetime.utcnow().isoformat(timespec="seconds"),
                    digest_day,
                ),
            )

    def mark_notified(self, web_sifra: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET notified_at = ? WHERE web_sifra = ?",
                (datetime.utcnow().isoformat(timespec="seconds"), web_sifra),
            )

    def jobs_for_digest_day(self, day: int) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM jobs WHERE digest_day = ? ORDER BY deadline_date IS NULL, deadline_date",
            (day,),
        )
        return cur.fetchall()

    def unnotified_expiring_within(self, hours: int = config.URGENT_WITHIN_HOURS) -> list[sqlite3.Row]:
        cutoff = (datetime.utcnow() + timedelta(hours=hours)).date().isoformat()
        cur = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE notified_at IS NULL
              AND deadline_date IS NOT NULL
              AND deadline_date <= ?
            ORDER BY deadline_date
            """,
            (cutoff,),
        )
        return cur.fetchall()

    def prune_expired(self, before: date | None = None) -> int:
        """Drop rows whose deadline has passed, keeping the DB from growing forever."""
        cutoff = (before or date.today()).isoformat()
        with self.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE deadline_date IS NOT NULL AND deadline_date < ?",
                (cutoff,),
            )
            return cur.rowcount

    def get_meta(self, key: str) -> str | None:
        cur = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def days_since_last_success(self, today: date | None = None) -> int | None:
        """Calendar days since last successful collect, or None if never succeeded."""
        raw = self.get_meta("last_successful_collect_on")
        if not raw:
            return None
        last = date.fromisoformat(raw)
        today = today or date.today()
        return (today - last).days

    def mark_collect_success(self, today: date | None = None) -> None:
        self.set_meta("last_successful_collect_on", (today or date.today()).isoformat())

    def mark_full_scrape_success(self, today: date | None = None) -> None:
        self.set_meta("last_full_scrape_on", (today or date.today()).isoformat())

    def count_jobs(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM jobs")
        return int(cur.fetchone()["n"])

    def count_unnotified(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE notified_at IS NULL"
        )
        return int(cur.fetchone()["n"])

    def count_inspected(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS n FROM inspected")
        return int(cur.fetchone()["n"])

    def count_details_fetched(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM inspected WHERE detail_fetched = 1"
        )
        return int(cur.fetchone()["n"])

    def count_pending_details(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM inspected WHERE detail_fetched = 0"
        )
        return int(cur.fetchone()["n"])

    def is_detail_fetched(self, web_sifra: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM inspected WHERE web_sifra = ? AND detail_fetched = 1 LIMIT 1",
            (web_sifra,),
        )
        return cur.fetchone() is not None

    def record_listing(self, listing, category_label: str | None = None) -> None:
        label = category_label or getattr(listing, "category_label", "") or ""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO inspected (
                    web_sifra, title, employer, location_raw, deadline_raw,
                    detail_url, category_label, listed_at, detail_fetched
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(web_sifra) DO UPDATE SET
                    title=excluded.title,
                    employer=excluded.employer,
                    location_raw=excluded.location_raw,
                    deadline_raw=excluded.deadline_raw,
                    detail_url=excluded.detail_url,
                    category_label=COALESCE(excluded.category_label, inspected.category_label)
                """,
                (
                    listing.web_sifra,
                    listing.title,
                    listing.employer,
                    listing.location_raw,
                    listing.deadline_raw,
                    listing.detail_url,
                    label,
                    datetime.utcnow().isoformat(timespec="seconds"),
                ),
            )

    def mark_detail_fetched(
        self,
        web_sifra: str,
        matched: bool,
        skip_reason: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE inspected
                SET detail_fetched = 1,
                    detail_fetched_at = ?,
                    matched = ?,
                    skip_reason = ?
                WHERE web_sifra = ?
                """,
                (
                    datetime.utcnow().isoformat(timespec="seconds"),
                    1 if matched else 0,
                    skip_reason,
                    web_sifra,
                ),
            )

    def pending_inspected(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT * FROM inspected
            WHERE detail_fetched = 0
            ORDER BY listed_at, web_sifra
        """
        params: tuple = ()
        if limit and limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        return self._conn.execute(sql, params).fetchall()

    def all_unnotified_jobs(self) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE notified_at IS NULL
            ORDER BY deadline_date IS NULL, deadline_date
            """
        )
        return cur.fetchall()

    def latest_scrape_run(self) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1"
        )
        return cur.fetchone()

    def get_scrape_run(self, run_id: int) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM scrape_runs WHERE id = ?", (run_id,)
        )
        return cur.fetchone()

    def start_scrape_run(self) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO scrape_runs (started_at) VALUES (?)",
                (datetime.utcnow().isoformat(timespec="seconds"),),
            )
            return int(cur.lastrowid)

    def mark_run_list_complete(self, run_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE scrape_runs
                SET list_completed_at = ?
                WHERE id = ? AND list_completed_at IS NULL
                """,
                (datetime.utcnow().isoformat(timespec="seconds"), run_id),
            )

    def mark_run_details_complete(self, run_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE scrape_runs
                SET details_completed_at = ?
                WHERE id = ? AND details_completed_at IS NULL
                """,
                (datetime.utcnow().isoformat(timespec="seconds"), run_id),
            )

    def mark_run_notify_complete(self, run_id: int) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE scrape_runs
                SET notify_completed_at = ?
                WHERE id = ? AND notify_completed_at IS NULL
                """,
                (datetime.utcnow().isoformat(timespec="seconds"), run_id),
            )

    def is_category_complete(self, run_id: int, event_target: str) -> bool:
        cur = self._conn.execute(
            """
            SELECT 1 FROM scrape_categories
            WHERE run_id = ? AND event_target = ?
            LIMIT 1
            """,
            (run_id, event_target),
        )
        return cur.fetchone() is not None

    def mark_category_complete(
        self, run_id: int, event_target: str, label: str = ""
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scrape_categories (run_id, event_target, label, completed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, event_target) DO UPDATE SET
                    label=excluded.label,
                    completed_at=excluded.completed_at
                """,
                (
                    run_id,
                    event_target,
                    label,
                    datetime.utcnow().isoformat(timespec="seconds"),
                ),
            )

    def count_completed_categories(self, run_id: int) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM scrape_categories WHERE run_id = ?",
            (run_id,),
        )
        return int(cur.fetchone()["n"])
