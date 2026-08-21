"""
main.py
CLI entrypoint. Two subcommands:

    python main.py bootstrap   # one-time: queue existing matches into a 6-day review
    python main.py daily       # cron target: collect new matches every day, publish them

Wrapped in a top-level try/except so a cron-triggered failure logs a full
traceback and exits non-zero (visible in GitHub Actions / cron mail) instead
of dying silently.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

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


def load_local_secrets() -> None:
    """Load `.env` from the repo root if present. Does not override existing env vars
    (so GitHub Actions secrets win)."""
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path, override=False)


def get_telegram_token() -> str:
    token = os.environ.get(config.TELEGRAM_ENV_TOKEN)
    if not token:
        raise RuntimeError(
            f"Set {config.TELEGRAM_ENV_TOKEN} in `.env` (see `.env.example`) "
            "or as an environment variable."
        )
    return token


def get_telegram_creds() -> tuple[str, str]:
    token = get_telegram_token()
    chat_id = os.environ.get(config.TELEGRAM_ENV_CHAT_ID)
    if not chat_id:
        raise RuntimeError(
            f"Set {config.TELEGRAM_ENV_TOKEN} and {config.TELEGRAM_ENV_CHAT_ID} "
            "in a local `.env` file (see `.env.example`) or as environment "
            "variables (GitHub Actions: repo secrets). "
            "Run `python main.py chat-id` after messaging the bot to find your chat id."
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


def seed_backlog(listings: list, store: StateStore) -> dict[int, list]:
    buckets = digest.build_initial_digest(listings)
    for day, jobs in buckets.items():
        for job in jobs:
            store.upsert_job(job, digest_day=day)
        log.info("Backlog day %d: %d jobs", day, len(jobs))
    return buckets


def should_publish_new_matches(today: date | None = None) -> bool:
    """Collection is daily; publishing new matches can later be weekly via config."""
    cadence = (config.NEW_MATCH_PUBLISH_CADENCE or "daily").lower()
    if cadence == "weekly":
        today = today or date.today()
        return today.weekday() == config.NEW_MATCH_PUBLISH_WEEKDAY
    return True


def run_bootstrap() -> None:
    session = build_session()
    with StateStore() as store:
        listings = collect_and_score(session, store, skip_seen=False)
        log.info("Bootstrap: %d matching Zagreb listings found.", len(listings))
        seed_backlog(listings, store)
        # Dispatch is left to `daily` so launch-week pacing stays one bucket/day.


def run_daily() -> None:
    session = build_session()
    token, chat_id = get_telegram_creds()
    with StateStore() as store:
        if store.is_empty():
            log.info("Empty state DB — seeding existing matches as backlog, not as 'new'.")
            listings = collect_and_score(session, store, skip_seen=False)
            log.info("Initial seed: %d matching Zagreb listings.", len(listings))
            seed_backlog(listings, store)
            notify.send_new_matches_report(token, chat_id, [])
            _publish_next_backlog_day(store, token, chat_id)
            removed = store.prune_expired()
            if removed:
                log.info("Pruned %d expired listings from the state DB.", removed)
            return

        new_listings = collect_and_score(session, store, skip_seen=True)
        log.info("Daily collect: %d newly discovered matching listings.", len(new_listings))
        for job in new_listings:
            store.upsert_job(job, digest_day=None)

        if should_publish_new_matches():
            rows = store.unnotified_new_matches()
            if rows or config.NOTIFY_WHEN_NO_NEW_MATCHES:
                notify.send_new_matches_report(token, chat_id, rows)
            for row in rows:
                store.mark_notified(row["web_sifra"])
        else:
            log.info(
                "Collect-only day (NEW_MATCH_PUBLISH_CADENCE=%s); %d unpublished new matches stored.",
                config.NEW_MATCH_PUBLISH_CADENCE,
                len(store.unnotified_new_matches()),
            )

        _publish_next_backlog_day(store, token, chat_id)

        removed = store.prune_expired()
        if removed:
            log.info("Pruned %d expired listings from the state DB.", removed)


def _publish_next_backlog_day(store: StateStore, token: str, chat_id: str) -> None:
    """Send the next unsent 6-day backlog bucket, if any remain."""
    today_bucket_day = _current_bootstrap_day(store)
    if not today_bucket_day:
        return
    rows = [r for r in store.jobs_for_digest_day(today_bucket_day) if r["notified_at"] is None]
    if not rows:
        return
    notify.send_telegram_digest(
        token, chat_id, f"Existing listings — day {today_bucket_day}", rows
    )
    for row in rows:
        store.mark_notified(row["web_sifra"])


def _current_bootstrap_day(store: StateStore) -> int | None:
    for day in range(1, config.DIGEST_DAYS + 1):
        rows = store.jobs_for_digest_day(day)
        if any(r["notified_at"] is None for r in rows):
            return day
    return None


def run_chat_id() -> None:
    token = get_telegram_token()
    print(
        "This bot does not reply in Telegram until we send a digest or ping.\n"
        "Open YOUR bot (not @BotFather), tap Start, send hi, then this command\n"
        "will print the chat id and send a one-line confirmation.\n"
    )
    chats = notify.fetch_chat_ids(token)
    if not chats:
        print(
            "No chats yet. In Telegram:\n"
            "  1. Open the bot you created (the one whose token is in .env)\n"
            "  2. Tap Start / send hi — it will stay silent; that is normal\n"
            "  3. Run: python main.py chat-id\n"
        )
        return
    print("Chats that have talked to this bot:")
    for chat in chats:
        print(f"  TELEGRAM_CHAT_ID={chat['id']}  ({chat['type']}: {chat['title']})")
        try:
            notify.send_connection_ping(token, chat["id"])
            print(f"  Sent a confirmation ping to {chat['id']}. Check Telegram.")
        except Exception:
            log.exception("Could not ping chat %s", chat["id"])


def run_telegram_check() -> None:
    token = get_telegram_token()
    chat_id = os.environ.get(config.TELEGRAM_ENV_CHAT_ID)
    if not chat_id:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing. Add it as a GitHub Actions secret "
            "or in `.env`, then re-run."
        )
    info = notify.verify_telegram_connection(token, chat_id)
    print(
        "Telegram secrets are valid.\n"
        f"  bot: @{info['bot_username']} (id {info['bot_id']})\n"
        f"  chat: {info['chat_id']} ({info['chat_type']}: {info['chat_name']})\n"
        "No message was sent."
    )


def main() -> None:
    setup_logging()
    load_local_secrets()
    parser = argparse.ArgumentParser(description="HZZ Zagreb foreign-friendly job digest")
    parser.add_argument("mode", choices=["bootstrap", "daily", "chat-id", "telegram-check"])
    args = parser.parse_args()

    try:
        if args.mode == "bootstrap":
            run_bootstrap()
        elif args.mode == "chat-id":
            run_chat_id()
        elif args.mode == "telegram-check":
            run_telegram_check()
        else:
            run_daily()
    except Exception:
        log.exception("Pipeline run failed (mode=%s)", args.mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
