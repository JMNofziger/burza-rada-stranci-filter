"""
main.py
CLI entrypoint. Two subcommands:

    python -m main bootstrap   # one-time initial 6-day digest build
    python -m main daily       # ongoing incremental daily run (cron target)

Wrapped in a top-level try/except so a cron-triggered failure logs a full
traceback and exits non-zero (visible in GitHub Actions / cron mail) instead
of dying silently -- the reference design had no error handling at all,
which for an unattended daily job means failures go unnoticed indefinitely.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

import config
import digest
import notify
import scoring
from http_client import build_session
from scraper import fetch_detail, iter_zagreb_candidates
from storage import StateStore

log = logging.getLogger("hzz_pipeline")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def get_telegram_creds() -> tuple[str, str]:
    token = os.environ.get(config.TELEGRAM_ENV_TOKEN)
    chat_id = os.environ.get(config.TELEGRAM_ENV_CHAT_ID)
    if not token or not chat_id:
        raise RuntimeError(
            f"Set {config.TELEGRAM_ENV_TOKEN} and {config.TELEGRAM_ENV_CHAT_ID} "
            "as environment variables (GitHub Actions: repo secrets)."
        )
    return token, chat_id


def collect_and_score(session, store: StateStore, skip_seen: bool) -> list:
    """Shared scrape+score step for both bootstrap and daily runs."""
    results = []
    for candidate in iter_zagreb_candidates(session):
        if skip_seen and store.is_seen(candidate.web_sifra):
            continue
        detailed = fetch_detail(session, candidate)
        if detailed is None:
            continue  # already logged inside fetch_detail
        full_text = f"{detailed.title} {detailed.employer} {detailed.description}"
        detailed.foreign_score, detailed.matched_keywords = scoring.score_foreign_friendly(full_text)
        detailed.location_score = scoring.score_location(
            detailed.location_raw,
            detailed.description,
            in_zagreb_county=True,
        )
        if detailed.foreign_score >= config.FOREIGN_SCORE_THRESHOLD and detailed.location_score > 0:
            results.append(detailed)
    return results


def run_bootstrap() -> None:
    session = build_session()
    with StateStore() as store:
        listings = collect_and_score(session, store, skip_seen=False)
        log.info("Bootstrap: %d matching Zagreb listings found.", len(listings))
        buckets = digest.build_initial_digest(listings)
        for day, jobs in buckets.items():
            for job in jobs:
                store.upsert_job(job, digest_day=day)
            log.info("Day %d: %d jobs", day, len(jobs))
        # Dispatch is deliberately left to a separate "daily" invocation per
        # day so the launch-week pacing described in the spec is honoured --
        # bootstrap only *builds* the queue, `daily` sends whatever the
        # current day's bucket is the first time it runs.


def run_daily() -> None:
    session = build_session()
    token, chat_id = get_telegram_creds()
    with StateStore() as store:
        new_listings = collect_and_score(session, store, skip_seen=True)
        log.info("Daily run: %d newly discovered matching listings.", len(new_listings))
        for job in new_listings:
            store.upsert_job(job, digest_day=None)

        urgent_rows = store.unnotified_expiring_within(config.URGENT_WITHIN_HOURS)
        if urgent_rows:
            notify.send_telegram_digest(token, chat_id, "🚨 Hitno (< 48h)", urgent_rows)
            for row in urgent_rows:
                store.mark_notified(row["web_sifra"])

        # Send today's bootstrap bucket, if this is still launch week and it
        # hasn't been sent yet.
        today_bucket_day = _current_bootstrap_day(store)
        if today_bucket_day:
            rows = [
                r for r in store.jobs_for_digest_day(today_bucket_day) if r["notified_at"] is None
            ]
            if rows:
                notify.send_telegram_digest(token, chat_id, f"Day {today_bucket_day}", rows)
                for row in rows:
                    store.mark_notified(row["web_sifra"])

        removed = store.prune_expired()
        if removed:
            log.info("Pruned %d expired listings from the state DB.", removed)


def _current_bootstrap_day(store: StateStore) -> int | None:
    """
    Minimal placeholder: figure out which of the 6 launch-week digest_day
    buckets is 'due' today. A real deployment should persist a
    'launch_date'/'last_dispatched_day' value (e.g. a one-row settings table)
    rather than inferring it -- left simple here since it's orthogonal to the
    scraping/scoring/dedup concerns this package focuses on.
    """
    for day in range(1, config.DIGEST_DAYS + 1):
        rows = store.jobs_for_digest_day(day)
        if any(r["notified_at"] is None for r in rows):
            return day
    return None


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="HZZ Zagreb foreign-friendly job digest")
    parser.add_argument("mode", choices=["bootstrap", "daily"])
    args = parser.parse_args()

    try:
        if args.mode == "bootstrap":
            run_bootstrap()
        else:
            run_daily()
    except Exception:
        log.exception("Pipeline run failed (mode=%s)", args.mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
