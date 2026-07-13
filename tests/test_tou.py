"""Stage 8: boundary intervals classify on interval START, weekends are
offpeak, and Youth Day (16 June 2026) stays a normal weekday."""

from pathlib import Path

import pandas as pd

from etana.canonical import month_grid
from etana.config import load_config
from etana.tou import classify

REPO = Path(__file__).resolve().parents[1]
CFG = load_config(REPO / "config" / "allocation_config.json")
GRID = month_grid(2026, 6, CFG.timezone)
BUCKETS = classify(GRID, CFG.tou)


def bucket_at(text: str) -> str:
    return BUCKETS[pd.Timestamp(text, tz="Africa/Johannesburg")]


def test_boundary_labels_classify_on_interval_start():
    # 1 June 2026 is a Monday. The label names the interval's END:
    # label 07:00 covers 06:30-07:00 -> Standard, NOT Peak.
    # Errors here move money.
    assert bucket_at("2026-06-01 06:00") == "offpeak"   # covers 05:30-06:00
    assert bucket_at("2026-06-01 06:30") == "standard"  # covers 06:00-06:30
    assert bucket_at("2026-06-01 07:00") == "standard"  # covers 06:30-07:00
    assert bucket_at("2026-06-01 07:30") == "peak"      # covers 07:00-07:30
    assert bucket_at("2026-06-01 10:00") == "peak"      # covers 09:30-10:00
    assert bucket_at("2026-06-01 10:30") == "standard"  # covers 10:00-10:30
    assert bucket_at("2026-06-01 18:30") == "peak"      # covers 18:00-18:30
    assert bucket_at("2026-06-01 20:00") == "peak"      # covers 19:30-20:00
    assert bucket_at("2026-06-01 20:30") == "standard"  # covers 20:00-20:30
    assert bucket_at("2026-06-01 22:00") == "standard"  # covers 21:30-22:00
    assert bucket_at("2026-06-01 22:30") == "offpeak"   # covers 22:00-22:30


def test_weekend_is_entirely_offpeak():
    saturday = pd.Timestamp("2026-06-06", tz="Africa/Johannesburg")  # Sat
    day = BUCKETS[(BUCKETS.index > saturday) & (BUCKETS.index <= saturday + pd.Timedelta(days=1))]
    assert (day == "offpeak").all()
    assert bucket_at("2026-06-07 08:30") == "offpeak"  # Sunday morning "peak" hour


def test_public_holiday_is_a_normal_weekday():
    # Youth Day, Tue 16 June 2026. Config says holidays are normal weekdays,
    # and no holidays library exists to disagree. Pin the whole day's profile
    # to another Tuesday so a future dependency cannot silently change it.
    def day_profile(date: str) -> list[str]:
        start = pd.Timestamp(date, tz="Africa/Johannesburg")
        day = BUCKETS[(BUCKETS.index > start) & (BUCKETS.index <= start + pd.Timedelta(days=1))]
        return list(day)

    assert bucket_at("2026-06-16 08:00") == "peak"  # would be offpeak if holiday-aware
    assert day_profile("2026-06-16") == day_profile("2026-06-23")


def test_month_edges_and_bucket_accounting():
    # First label (ends 00:30 Mon 1 June) and last (ends 00:00 Wed 1 July,
    # covering Tue 23:30-24:00) are both offpeak.
    assert BUCKETS.iloc[0] == "offpeak" and BUCKETS.iloc[-1] == "offpeak"
    counts = BUCKETS.value_counts()
    assert int(counts.sum()) == 1440
    # 22 weekdays in June 2026. Peak = 3+2 = 5h/day; standard = 1+8+2 = 11h/day;
    # two intervals per hour.
    assert counts["peak"] == 22 * 5 * 2
    assert counts["standard"] == 22 * 11 * 2
    assert counts["offpeak"] == 1440 - 22 * 16 * 2
