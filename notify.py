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
MAX_MESSAGE_CHARS = 3500  # stay comfortably under Telegram's 4096 hard limit

_MD_ESCAPE_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: str) -> str:
    return re.sub(f"([{re.escape(_MD_ESCAPE_CHARS)}])", r"\\\1", text or "")


def format_job_line(job) -> str:
    title = escape_markdown_v2(job["title"])
    employer = escape_markdown_v2(job["employer"] or "N/A")
    deadline = escape_markdown_v2(job["deadline_date"] or "otvoreno")
    badge = "🏙️ Centar" if job["location_score"] == 2 else "Zagreb"
    url = job["detail_url"]
    return f"*{title}*\n{employer} · {badge} · rok: {deadline}\n[Otvori oglas]({url})"


def build_digest_message(day_label: str, jobs: list) -> list[str]:
    """Return one or more message chunks (Telegram has a per-message length cap)."""
    header = f"*{escape_markdown_v2(day_label)}* — {len(jobs)} oglasa\n\n"
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


def send_telegram_digest(token: str, chat_id: str, day_label: str, jobs: list) -> None:
    if not jobs:
        log.info("No jobs for %s -- skipping send.", day_label)
        return
    for chunk in build_digest_message(day_label, jobs):
        _send_with_retry(token, chat_id, chunk)


def _send_with_retry(token: str, chat_id: str, text: str, max_attempts: int = 3) -> None:
    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
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
