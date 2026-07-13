"""MeterFlow adapter: MTR-1001 and MTR-1002 onto the canonical grid.

The file is a stacked two-meter CSV — UTC ISO-8601 timestamps, 30-minute kWh,
interval-ending, comma-delimited, CRLF (contract confirmed against the raw
span in Stage 1). Everything MeterFlow-specific stays in this module.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..canonical import to_canonical
from .base import FeedContract, dedupe_last_wins, exclude_unparseable, read_feed_csv, window

CONTRACT = FeedContract(
    provider="MeterFlow",
    timezone="UTC",
    interval_minutes=30,
    unit="kwh",
    convention="ending",
)

_COLUMNS = {
    "meter_serial": "meter_id",
    "reading_timestamp_utc": "ts",
    "consumption_kwh": "kwh",
}


def load(path: Path, grid: pd.DatetimeIndex, findings: list | None = None) -> dict[str, pd.DataFrame]:
    """Read the stacked file and land each meter on the canonical grid."""
    frame = read_feed_csv(path, CONTRACT, required=list(_COLUMNS)).rename(columns=_COLUMNS)

    # Parse as UTC-aware, convert to the canonical zone, and only THEN window:
    # SAST June starts 2026-05-31 22:00 UTC, so windowing on raw UTC labels
    # would clip the month's first two hours and keep two hours of July.
    # Labels are already interval-ending, so conversion is the whole job.
    ts = pd.to_datetime(frame["ts"], utc=True, format="ISO8601", errors="coerce")
    frame = exclude_unparseable(
        frame.assign(interval_end=ts.dt.tz_convert(grid.tz)), ts, CONTRACT.provider, findings
    )

    frame = window(frame[["meter_id", "interval_end", "kwh"]], grid, CONTRACT.provider, findings)
    frame = dedupe_last_wins(frame, CONTRACT.provider, findings)

    # Stacked file: split by meter id — never by row order, and never assuming
    # which meters appear; reconciliation against config happens downstream.
    source = f"{CONTRACT.provider}:{path.name}"
    return {
        meter: to_canonical(meter, source, group.set_index("interval_end")[["kwh"]], grid)
        for meter, group in frame.groupby("meter_id", sort=True)
    }
