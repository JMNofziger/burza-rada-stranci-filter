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
                    matched_keywords=excluded.matched_keywords
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
