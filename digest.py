"""
digest.py
Bootstrap ("6-Day Digest") bucketing and ongoing incremental updates.

THE BUG THIS FIXES
--------------------
The reference implementation sorts the backlog by urgency (earliest deadline
first) -- correct -- but then assigns days with `idx % 6` round-robin:

    idx 0 (most urgent)  -> Day 1
    idx 1                -> Day 2
    idx 2                -> Day 3
    ...
    idx 6                -> Day 1
    idx 7                -> Day 2

That interleaves urgency tiers across all six delivery days instead of
front-loading urgency into the earliest days. Concretely: if there are more
than 6 jobs, the 7th-most-urgent job still lands back in Day 1 (fine), but
the 2nd-most-urgent job is pushed to Day 2 even if its deadline is, say,
tomorrow -- and any job whose deadline falls before its assigned delivery
date is *dead on arrival*, shown to the user only after it already closed.

The fix: sort by urgency, then chunk SEQUENTIALLY (most-urgent chunk -> Day 1,
next chunk -> Day 2, ...), and additionally hard-guarantee that nothing with
a deadline inside the next 48 hours is ever placed later than Day 1,
regardless of chunk math -- mirroring the same 48h rule the spec already
requires for incremental daily updates, applied consistently at bootstrap
time too.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import config


def _sort_key(listing):
    # None (open-ended) deadlines sort last; ties broken by higher location
    # score (city centre) first.
    far_future = date.max
    return (listing.deadline_date or far_future, -listing.location_score)


def build_initial_digest(listings: list, today: date | None = None) -> dict[int, list]:
    """
    Split a historical backlog into DIGEST_DAYS buckets (1..DIGEST_DAYS),
    most urgent jobs delivered soonest, with a 48h safety override.
    """
    today = today or date.today()
    sorted_listings = sorted(listings, key=_sort_key)

    n = len(sorted_listings)
    chunk_size = math.ceil(n / config.DIGEST_DAYS) if n else 0

    digest: dict[int, list] = {day: [] for day in range(1, config.DIGEST_DAYS + 1)}

    for idx, listing in enumerate(sorted_listings):
        natural_day = min(idx // chunk_size, config.DIGEST_DAYS - 1) + 1 if chunk_size else 1
        assigned_day = natural_day

        # Safety override: never let a <=48h-urgent job land later than the
        # delivery day whose calendar date is still before its deadline.
        if listing.deadline_date is not None:
            hours_until_deadline = (
                (listing.deadline_date - today).days * 24
            )
            if hours_until_deadline <= config.URGENT_WITHIN_HOURS:
                assigned_day = 1
            else:
                latest_safe_day = (listing.deadline_date - today).days
                assigned_day = min(assigned_day, max(1, latest_safe_day))

        digest[assigned_day].append(listing)

    return digest


def apply_incremental_update(
    new_listings: list,
    today_digest: list,
    today: date | None = None,
) -> list:
    """
    Merge newly-discovered jobs into *today's* dispatch. Anything closing
    within 48h is inserted at the top (matches spec D); everything else is
    appended, to go out with today's regular batch rather than being held
    back to a future day that may never see it before it closes.
    """
    today = today or date.today()
    urgent, normal = [], []

    for listing in new_listings:
        is_urgent = (
            listing.deadline_date is not None
            and (listing.deadline_date - today).days * 24 <= config.URGENT_WITHIN_HOURS
        )
        (urgent if is_urgent else normal).append(listing)

    urgent.sort(key=_sort_key)
    return urgent + today_digest + normal
