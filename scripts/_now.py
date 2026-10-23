"""One "now" for every script: CLE_NOW (ISO UTC) when set, else the wall clock.

    from _now import now, now_date, now_iso
    now()            # aware datetime, floored to the hour (the build epoch, as cle.timeutil.window)
    now('day')       # floored to 00:00 UTC (catalog / extractor reference date)
    now_date()       # datetime.date
    now_iso()        # '2026-10-21T13:00:00Z'

Scripts under scripts/ run with -I, which (safe_path) does NOT put the script directory on sys.path, so each
script inserts its own directory first: sys.path.insert(0, str(Path(__file__).resolve().parent)); from _now import now.
An explicit value beats the environment: now(value='2026-10-21T13:00:00Z').
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone


def now(floor: str = "hour", value: str | None = None) -> datetime:
    text = value or os.environ.get("CLE_NOW") or ""
    t = datetime.fromisoformat(text.replace("Z", "+00:00")) if text.strip() else datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return t.replace(hour=0) if floor == "day" else t


def now_date(value: str | None = None) -> date:
    return now("day", value).date()


def now_iso(floor: str = "hour", value: str | None = None) -> str:
    return now(floor, value).strftime("%Y-%m-%dT%H:%M:%SZ")
