"""Build epoch and data window (ARCHITECTURE.md AM2).

"Now" is the build time floored to the hour (UTC); the window is now - 15 d .. now + 15 d and every
grid is aligned to its start. Times are UTC seconds since 1970 (float64) throughout the pipeline;
no leap second falls inside a 30-day window except at the end of June or December.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

HALF_WINDOW_S = 15 * 86400


@dataclass(frozen=True)
class Window:
    now_s: float     # build "now", UTC seconds
    t0_s: float      # window start
    t1_s: float      # window end

    @property
    def now(self) -> datetime:
        return utc(self.now_s)

    @property
    def t0(self) -> datetime:
        return utc(self.t0_s)

    @property
    def t1(self) -> datetime:
        return utc(self.t1_s)

    def grid(self, step_s: float, t0_s: float | None = None, t1_s: float | None = None):
        """Uniform epochs from t0 to t1 inclusive, aligned to the window start."""
        import numpy as np
        a = self.t0_s if t0_s is None else t0_s
        b = self.t1_s if t1_s is None else t1_s
        k0 = int(-(-(a - self.t0_s) // step_s))           # first aligned index >= a
        k1 = int((b - self.t0_s) // step_s)
        return self.t0_s + step_s * np.arange(k0, k1 + 1, dtype=float)


def utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def iso(seconds: float) -> str:
    return utc(seconds).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def window(now: str | None = None, half_s: float = HALF_WINDOW_S) -> Window:
    """Window around `now` (ISO string), $CLE_NOW, or the wall clock, floored to the hour."""
    text = now or os.environ.get("CLE_NOW")
    t = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    n = t.timestamp()
    return Window(now_s=n, t0_s=n - half_s, t1_s=n + half_s)


def days(n: float) -> timedelta:
    return timedelta(days=n)
