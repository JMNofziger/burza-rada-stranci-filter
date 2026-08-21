"""Build the public jobs.json payload for GitHub Pages."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import config
from storage import StateStore

log = logging.getLogger(__name__)


def jobs_payload(
    store: StateStore,
    now: datetime | None = None,
    today: date | None = None,
) -> dict:
    generated = now or datetime.utcnow()
    as_of = today or (now.date() if now else date.today())
    return {
        "jobs": store.list_jobs(today=as_of),
        "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def write_jobs_json(
    store: StateStore,
    path: Path | None = None,
    now: datetime | None = None,
    today: date | None = None,
) -> Path:
    dest = path or config.JOBS_JSON_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = jobs_payload(store, now=now, today=today)
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log.info("Wrote %d matching jobs to %s.", len(payload["jobs"]), dest)
    return dest
