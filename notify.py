"""
notify.py
Telegram delivery. Chosen as the primary channel because it's free, push
notification-based (a real fit for "closes within 48h" urgency), supports
Markdown formatting + inline links, and needs no mail server/deliverability
setup for a personal tool. See README for the Email/webhook alternatives.
"""

from __future__ import annotations

import logging
import re
import time

import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_UPDATES_API = "https://api.telegram.org/bot{token}/getUpdates"
MAX_MESSAGE_CHARS = 3500  # stay comfortably under Telegram's 4096 hard limit

_MD_ESCAPE_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: str) -> str:
    return re.sub(f"([{re.escape(_MD_ESCAPE_CHARS)}])", r"\\\1", text or "")


def format_job_line(job) -> str:
    job = as_job_dict(job)
    title = escape_markdown_v2(job["title"])
    employer = escape_markdown_v2(job["employer"] or "N/A")
    deadline = escape_markdown_v2(job["deadline_date"] or "open")
    badge = "City centre" if job["location_score"] == 2 else "Zagreb"
    url = job["detail_url"]
    return f"*{title}*\n{employer} · {badge} · deadline: {deadline}\n[Open listing]({url})"


def as_job_dict(job) -> dict:
    if hasattr(job, "keys") and not isinstance(job, dict):
        job = dict(job)
    if isinstance(job, dict):
        return job
    deadline = getattr(job, "deadline_date", None)
    return {
        "title": job.title,
        "employer": job.employer,
        "deadline_date": deadline.isoformat() if deadline else None,
        "location_score": job.location_score,
        "detail_url": job.detail_url,
    }


def build_digest_message(day_label: str, jobs: list) -> list[str]:
    """Return one or more message chunks (Telegram has a per-message length cap)."""
    header = f"*{escape_markdown_v2(day_label)}* — {len(jobs)} listings\n\n"
    chunks, current = [], header
    for job in jobs:
        line = format_job_line(job) + "\n\n"
        if len(current) + len(line) > MAX_MESSAGE_CHARS:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


def build_zero_new_matches_message(title: str = "New listings") -> str:
    return (
        f"*{escape_markdown_v2(title)}*\n\n"
        "No new listings matched the filter today\\."
    )


def send_new_matches_report(
    token: str, chat_id: str, jobs: list, title: str = "New listings"
) -> None:
    """Publish today's (or accumulated) new filter matches, or an explicit zero notice."""
    if not jobs:
        log.info("No new filter matches — sending zero notice.")
        _send_with_retry(token, chat_id, build_zero_new_matches_message(title))
        return
    send_telegram_digest(token, chat_id, title, jobs)


def send_telegram_digest(token: str, chat_id: str, day_label: str, jobs: list) -> None:
    if not jobs:
        log.info("No jobs for %s -- skipping send.", day_label)
        return
    for chunk in build_digest_message(day_label, jobs):
        _send_with_retry(token, chat_id, chunk)


def _send_with_retry(
    token: str,
    chat_id: str,
    text: str,
    max_attempts: int = 3,
    parse_mode: str | None = "MarkdownV2",
) -> None:
    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    for attempt in range(1, max_attempts + 1):
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code == 200:
            return
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            log.warning("Telegram rate-limited us; sleeping %ss", retry_after)
            time.sleep(retry_after)
            continue
        log.error("Telegram send failed (%s): %s", resp.status_code, resp.text)
        resp.raise_for_status()
    raise RuntimeError(f"Telegram send failed after {max_attempts} attempts")


def send_connection_ping(token: str, chat_id: str) -> None:
    """One-shot confirmation. This bot does not otherwise reply to chat."""
    _send_with_retry(
        token,
        chat_id,
        "Connected. This bot does not reply to chat messages.\n"
        "You will receive the daily job digest here.\n"
        f"Chat ID: {chat_id}",
        parse_mode=None,
    )


def _chat_from_update(update: dict) -> dict | None:
    for key in ("message", "edited_message", "channel_post", "my_chat_member"):
        chat = (update.get(key) or {}).get("chat") or {}
        if chat.get("id") is not None:
            return chat
    callback_chat = ((update.get("callback_query") or {}).get("message") or {}).get("chat") or {}
    if callback_chat.get("id") is not None:
        return callback_chat
    return None


def fetch_chat_ids(token: str) -> list[dict]:
    """Return unique chats that have already messaged or started this bot."""
    resp = requests.get(TELEGRAM_UPDATES_API.format(token=token), timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram getUpdates failed: {payload}")
    chats: dict[str, dict] = {}
    for update in payload.get("result") or []:
        chat = _chat_from_update(update)
        if not chat:
            continue
        chat_id = chat["id"]
        chats[str(chat_id)] = {
            "id": chat_id,
            "type": chat.get("type") or "unknown",
            "title": chat.get("title") or chat.get("username") or chat.get("first_name") or "",
        }
    return list(chats.values())


def verify_telegram_connection(token: str, chat_id: str) -> dict:
    """Lightweight check: token is valid and chat_id is reachable. Does not send a message."""
    me = _telegram_get(token, "getMe")
    chat = _telegram_get(token, "getChat", {"chat_id": chat_id})
    return {
        "bot_username": me.get("username") or "",
        "bot_id": me.get("id"),
        "chat_id": chat.get("id"),
        "chat_type": chat.get("type") or "unknown",
        "chat_name": chat.get("title") or chat.get("username") or chat.get("first_name") or "",
    }


def _telegram_get(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    resp = requests.get(url, params=params or {}, timeout=20)
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Telegram {method} returned non-JSON ({resp.status_code})") from exc
    if not payload.get("ok"):
        description = payload.get("description") or payload
        raise RuntimeError(f"Telegram {method} failed: {description}")
    return payload.get("result") or {}


def send_critical_alert(token: str, chat_id: str, body: str) -> None:
    """Plain-text critical alert. Used when smoke/full collect fails."""
    text = (
        "CRITICAL: HZZ job digest\n\n"
        f"{body}\n\n"
        "The full scrape was not run (or did not finish). "
        "Unseen listings will be picked up on the next successful collect."
    )
    _send_with_retry(token, chat_id, text, parse_mode=None)
