"""
main.py
CLI entrypoint.

    python main.py bootstrap      # one-time: queue existing matches into a 6-day review
    python main.py daily          # cron target: collect new matches every day, publish them
    python main.py full-scrape    # resumable one-off complete scrape (see --phase)

Wrapped in a top-level try/except so a cron-triggered failure logs a full
traceback and exits non-zero (visible in GitHub Actions / cron mail) instead
of dying silently.
"""

from __future__ import annotations

import argparse
import json
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
from scraper import JobListing, fetch_detail, iter_zagreb_candidates, parse_hr_date
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


def _apply_scores(detailed: JobListing) -> bool:
    full_text = f"{detailed.title} {detailed.employer} {detailed.description}"
    detailed.foreign_score, detailed.matched_keywords = scoring.score_foreign_friendly(full_text)
    detailed.location_score = scoring.score_location(
        detailed.location_raw,
        detailed.description,
        in_zagreb_county=True,
    )
    return (
        detailed.foreign_score >= config.FOREIGN_SCORE_THRESHOLD
        and detailed.location_score > 0
    )


def collect_and_score(session, store: StateStore, skip_seen: bool) -> list:
    """Shared scrape+score step for both bootstrap and daily runs."""
    if config.IS_SMOKE or config.MAX_CATEGORIES or config.MAX_LISTINGS:
        log.info(
            "Limited collect: smoke=%s max_categories=%s max_listings=%s db=%s",
            config.IS_SMOKE,
            config.MAX_CATEGORIES or "all",
            config.MAX_LISTINGS or "all",
            config.DB_PATH,
        )
    results = []
    fetched = 0
    for candidate in iter_zagreb_candidates(session):
        store.record_listing(candidate)
        if store.is_detail_fetched(candidate.web_sifra):
            continue
        if skip_seen and store.is_seen(candidate.web_sifra):
            store.mark_detail_fetched(
                candidate.web_sifra, matched=True, skip_reason="already_in_jobs"
            )
            continue
        if config.MAX_LISTINGS and fetched >= config.MAX_LISTINGS:
            log.info("Hit HZZ_MAX_LISTINGS=%d; stopping collect.", config.MAX_LISTINGS)
            break
        detailed = fetch_detail(session, candidate)
        fetched += 1
        if detailed is None:
            continue  # leave inspected.detail_fetched=0 so a later run retries
        matched = _apply_scores(detailed)
        store.mark_detail_fetched(detailed.web_sifra, matched=matched)
        if matched:
            results.append(detailed)
    return results


def _telegram_label(text: str) -> str:
    return f"SMOKE TEST — {text}" if config.IS_SMOKE else text


def _new_listings_title(store: StateStore) -> str:
    gap = store.days_since_last_success()
    if gap is not None and gap >= 2:
        return _telegram_label(f"New listings — catch-up after {gap} days")
    return _telegram_label("New listings")


def _finish_successful_collect(store: StateStore) -> None:
    store.mark_collect_success()
    removed = store.prune_expired()
    if removed:
        log.info(
            "Pruned %d stale rows (jobs + inspected; %d-day dated / %d-day open-ended).",
            removed,
            config.EXPIRED_JOB_RETENTION_DAYS,
            config.OPEN_ENDED_JOB_RETENTION_DAYS,
        )


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
            notify.send_new_matches_report(
                token, chat_id, [], title=_new_listings_title(store)
            )
            _publish_next_backlog_day(store, token, chat_id)
            _finish_successful_collect(store)
            return

        new_listings = collect_and_score(session, store, skip_seen=True)
        log.info("Daily collect: %d newly discovered matching listings.", len(new_listings))
        for job in new_listings:
            store.upsert_job(job, digest_day=None)

        if should_publish_new_matches():
            rows = store.unnotified_new_matches()
            if rows or config.NOTIFY_WHEN_NO_NEW_MATCHES:
                notify.send_new_matches_report(
                    token, chat_id, rows, title=_new_listings_title(store)
                )
            for row in rows:
                store.mark_notified(row["web_sifra"])
        else:
            log.info(
                "Collect-only day (NEW_MATCH_PUBLISH_CADENCE=%s); %d unpublished new matches stored.",
                config.NEW_MATCH_PUBLISH_CADENCE,
                len(store.unnotified_new_matches()),
            )

        _publish_next_backlog_day(store, token, chat_id)
        _finish_successful_collect(store)


def _publish_next_backlog_day(store: StateStore, token: str, chat_id: str) -> None:
    """Send the next unsent 6-day backlog bucket, if any remain."""
    today_bucket_day = _current_bootstrap_day(store)
    if not today_bucket_day:
        return
    rows = [r for r in store.jobs_for_digest_day(today_bucket_day) if r["notified_at"] is None]
    if not rows:
        return
    notify.send_telegram_digest(
        token, chat_id, _telegram_label(f"Existing listings — day {today_bucket_day}"), rows
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


def run_smoke() -> None:
    """Cheap live scrape probe. No digest, no production DB writes."""
    config.MAX_CATEGORIES = config.MAX_CATEGORIES or 1
    config.MAX_LISTINGS = config.MAX_LISTINGS or 5
    session = build_session()
    candidates = []
    for listing in iter_zagreb_candidates(session):
        candidates.append(listing)
        if len(candidates) >= 3:
            break
    if not candidates:
        raise RuntimeError(
            "Smoke failed: 0 Grad Zagreb list rows. Site markup or session likely broke."
        )
    detailed = fetch_detail(session, candidates[0])
    if detailed is None or not detailed.description:
        raise RuntimeError(
            f"Smoke failed: detail page empty for WebSifra={candidates[0].web_sifra}."
        )
    log.info(
        "Smoke OK: %d list rows sampled, detail WebSifra=%s (%d chars).",
        len(candidates),
        detailed.web_sifra,
        len(detailed.description),
    )
    print(
        f"Smoke OK: {len(candidates)} list rows, "
        f"detail {detailed.web_sifra} ({len(detailed.description)} chars)."
    )


def run_alert_critical(message: str = "") -> None:
    body = (message or "").strip() or "HZZ digest failed. See GitHub Actions logs."
    token, chat_id = get_telegram_creds()
    notify.send_critical_alert(token, chat_id, body)
    print("Critical alert sent.")


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


def job_row_to_listing(row) -> JobListing:
    deadline = date.fromisoformat(row["deadline_date"]) if row["deadline_date"] else None
    keywords = [k for k in (row["matched_keywords"] or "").split(",") if k]
    return JobListing(
        web_sifra=row["web_sifra"],
        title=row["title"],
        employer=row["employer"] or "",
        location_raw=row["location_raw"] or "",
        deadline_raw="",
        detail_url=row["detail_url"] or "",
        deadline_date=deadline,
        foreign_score=row["foreign_score"] or 0,
        location_score=row["location_score"] or 0,
        matched_keywords=keywords,
    )


def inspected_row_to_listing(row) -> JobListing:
    listing = JobListing(
        web_sifra=row["web_sifra"],
        title=row["title"] or "",
        employer=row["employer"] or "",
        location_raw=row["location_raw"] or "",
        deadline_raw=row["deadline_raw"] or "",
        detail_url=row["detail_url"]
        or config.DETAIL_URL_TEMPLATE.format(web_sifra=row["web_sifra"]),
        category_label=row["category_label"] or "",
    )
    listing.deadline_date = parse_hr_date(listing.deadline_raw)
    return listing


def resolve_scrape_run(store: StateStore, reset_list: bool = False) -> int:
    latest = store.latest_scrape_run()
    if reset_list or latest is None:
        run_id = store.start_scrape_run()
        log.info("Started scrape run %s (reset_list=%s).", run_id, reset_list)
        return run_id
    if latest["notify_completed_at"]:
        run_id = store.start_scrape_run()
        log.info(
            "Previous scrape run %s already finished; started run %s.",
            latest["id"],
            run_id,
        )
        return run_id
    log.info("Resuming scrape run %s.", latest["id"])
    return int(latest["id"])


def full_scrape_status_dict(store: StateStore) -> dict:
    run = store.latest_scrape_run()
    list_complete = bool(run and run["list_completed_at"])
    details_pending = store.count_pending_details()
    unnotified = store.count_unnotified()
    notify_complete = bool(run and run["notify_completed_at"])
    if run is None or not list_complete:
        suggested = "list"
    elif details_pending > 0:
        suggested = "details"
    elif not notify_complete or unnotified > 0:
        suggested = "notify"
    else:
        suggested = "done"
    return {
        "suggested_phase": suggested,
        "open_run_id": int(run["id"]) if run else None,
        "list_complete": list_complete,
        "details_complete": bool(run and run["details_completed_at"]),
        "notify_complete": notify_complete,
        "categories_done": store.count_completed_categories(int(run["id"])) if run else 0,
        "listings": store.count_inspected(),
        "details_pending": details_pending,
        "details_fetched": store.count_details_fetched(),
        "jobs": store.count_jobs(),
        "unnotified": unnotified,
    }


def run_full_scrape_status() -> dict:
    with StateStore() as store:
        status = full_scrape_status_dict(store)
    print(json.dumps(status, sort_keys=True))
    return status


def run_full_scrape_list(reset_list: bool = False) -> None:
    with StateStore() as store:
        run_id = resolve_scrape_run(store, reset_list=reset_list)
        run = store.get_scrape_run(run_id)
        if run and run["list_completed_at"] and not reset_list:
            log.info("List phase already complete for scrape run %s.", run_id)
            return
        session = build_session()
        recorded = 0

        def skip_category(event_target: str, label: str) -> bool:
            return store.is_category_complete(run_id, event_target)

        def on_complete(event_target: str, label: str) -> None:
            store.mark_category_complete(run_id, event_target, label)
            log.info("Checkpoint: category complete: %s", label)

        for listing in iter_zagreb_candidates(
            session,
            skip_category=skip_category,
            on_category_complete=on_complete,
        ):
            store.record_listing(listing)
            recorded += 1
        cats_done = store.count_completed_categories(run_id)
        if recorded == 0 and cats_done == 0:
            raise RuntimeError(
                "List phase recorded 0 listings and finished 0 occupation categories. "
                "Site markup or session likely broke; not marking list complete."
            )
        store.mark_run_list_complete(run_id)
        log.info(
            "List phase done for run %s: recorded %d listings this walk, %d inspected total.",
            run_id,
            recorded,
            store.count_inspected(),
        )


def run_full_scrape_details(limit: int = 0) -> int:
    session = build_session()
    fetched = 0
    matched_n = 0
    with StateStore() as store:
        pending = store.pending_inspected(limit if limit and limit > 0 else None)
        log.info("Details phase: %d pending listings in this batch.", len(pending))
        for row in pending:
            listing = inspected_row_to_listing(row)
            detailed = fetch_detail(session, listing)
            if detailed is None:
                continue
            fetched += 1
            matched = _apply_scores(detailed)
            store.mark_detail_fetched(detailed.web_sifra, matched=matched)
            if matched:
                store.upsert_job(detailed, digest_day=None)
                matched_n += 1
        remaining = store.count_pending_details()
        if remaining == 0:
            run = store.latest_scrape_run()
            if run and not run["notify_completed_at"]:
                store.mark_run_details_complete(int(run["id"]))
        log.info(
            "Details batch done: fetched=%d matched=%d remaining_pending=%d.",
            fetched,
            matched_n,
            remaining,
        )
    return fetched


def run_full_scrape_notify() -> None:
    token, chat_id = get_telegram_creds()
    with StateStore() as store:
        never_collected = store.get_meta("last_successful_collect_on") is None
        if never_collected:
            rows = store.all_unnotified_jobs()
            if rows:
                listings = [job_row_to_listing(r) for r in rows]
                log.info(
                    "First fill: seeding %d matches into a 6-day backlog instead of one Telegram flood.",
                    len(listings),
                )
                seed_backlog(listings, store)
            notify.send_new_matches_report(
                token, chat_id, [], title=_new_listings_title(store)
            )
            _publish_next_backlog_day(store, token, chat_id)
        else:
            if should_publish_new_matches():
                rows = store.unnotified_new_matches()
                if rows or config.NOTIFY_WHEN_NO_NEW_MATCHES:
                    notify.send_new_matches_report(
                        token, chat_id, rows, title=_new_listings_title(store)
                    )
                for row in rows:
                    store.mark_notified(row["web_sifra"])
            _publish_next_backlog_day(store, token, chat_id)
        _finish_successful_collect(store)
        store.mark_full_scrape_success()
        run = store.latest_scrape_run()
        if run:
            store.mark_run_notify_complete(int(run["id"]))
        log.info("Notify phase complete.")


def run_full_scrape(phase: str, limit: int = 0, reset_list: bool = False) -> None:
    if phase == "status":
        run_full_scrape_status()
        return
    if phase == "list":
        run_full_scrape_list(reset_list=reset_list)
        return
    if phase == "details":
        run_full_scrape_details(limit=limit)
        return
    if phase == "notify":
        run_full_scrape_notify()
        return
    if phase != "all":
        raise ValueError(f"Unknown full-scrape phase: {phase}")
    run_full_scrape_list(reset_list=reset_list)
    while True:
        with StateStore() as store:
            pending = store.count_pending_details()
        if pending <= 0:
            break
        batch = limit if limit and limit > 0 else pending
        run_full_scrape_details(limit=batch)
    run_full_scrape_notify()


def main() -> None:
    setup_logging()
    load_local_secrets()
    parser = argparse.ArgumentParser(description="HZZ Zagreb foreign-friendly job digest")
    parser.add_argument(
        "mode",
        choices=[
            "bootstrap",
            "daily",
            "chat-id",
            "telegram-check",
            "smoke",
            "alert-critical",
            "full-scrape",
        ],
    )
    parser.add_argument("message", nargs="?", default="", help="Alert body for alert-critical")
    parser.add_argument(
        "--phase",
        default="status",
        choices=["all", "list", "details", "notify", "status"],
        help="full-scrape phase (default: status)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="full-scrape details: max listings this batch (0 = all remaining)",
    )
    parser.add_argument(
        "--reset-list",
        action="store_true",
        help="full-scrape list: start a new occupation walk, ignoring leftover category checkpoints",
    )
    args = parser.parse_args()

    try:
        if args.mode == "bootstrap":
            run_bootstrap()
        elif args.mode == "chat-id":
            run_chat_id()
        elif args.mode == "telegram-check":
            run_telegram_check()
        elif args.mode == "smoke":
            run_smoke()
        elif args.mode == "alert-critical":
            run_alert_critical(args.message)
        elif args.mode == "full-scrape":
            run_full_scrape(args.phase, limit=args.limit, reset_list=args.reset_list)
        else:
            run_daily()
    except Exception:
        log.exception("Pipeline run failed (mode=%s)", args.mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
