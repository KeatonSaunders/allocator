"""Time-of-use classification: one pure, vectorised function, config-driven.

Buckets are defined on SAST wall-clock time, weekdays only; weekends and
uncovered weekday hours fall to the config's complement bucket. Public
holidays are normal weekdays BY CONFIG — June 2026 contains Youth Day
(Tue 16th), and no holidays library is imported, so nothing can silently
reclassify a day and change the invoice; a test pins that.

The judgement call that moves real money: rows carry interval-ENDING labels,
so the label 07:00 covers 06:30-07:00 and belongs to Standard, not Peak.
Classification therefore uses the interval START (label - 30 min) against the
half-open [window.start, window.end) config windows.
"""

from __future__ import annotations

import pandas as pd

from .canonical import STEP
from .config import TouScheme


def classify(index: pd.DatetimeIndex, tou: TouScheme) -> pd.Series:
    """TOU bucket for each interval-ending label in `index`."""
    starts = index - STEP
    is_weekday = starts.weekday < 5  # Mon..Fri = 0..4; holidays intentionally ignored
    time_of_day = starts.time
    buckets = pd.Series(tou.complement_bucket, index=index, name="tou")
    # Windows were validated non-overlapping at config load, so the
    # assignment order across buckets cannot matter.
    for bucket, windows in tou.weekday_windows.items():
        for w in windows:
            inside = (time_of_day >= w.start) & (time_of_day < w.end)
            buckets[is_weekday & inside] = bucket
    return buckets
