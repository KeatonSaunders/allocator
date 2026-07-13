"""PowerTrack adapter: PT-77 onto the canonical grid.

The file is semicolon-delimited with split DD/MM/YYYY + HH:MM columns, naive
SAST timestamps, 15-minute average kW, interval-BEGINNING (contract confirmed
against the raw span in Stage 1). Three transforms, in this order:

1. convention: beginning -> ending, before any join or aggregation;
2. unit: average kW -> kWh;
3. length: two 15-min kWh halves -> one 30-min interval-ending bucket.

Everything PowerTrack-specific stays in this module.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..canonical import STEP, QualityFlag, to_canonical
from ..log import kv
from ..quality import Severity, note
from .base import FeedContract, dedupe_last_wins, exclude_unparseable, read_feed_csv, window

log = logging.getLogger("etana.adapters.powertrack")

CONTRACT = FeedContract(
    provider="PowerTrack",
    timezone="Africa/Johannesburg",
    interval_minutes=15,
    unit="kw",
    convention="beginning",
    delimiter=";",
)

FILE_GLOB = "powertrack_*.csv"  # provider file naming is a provider quirk too

_COLUMNS = {"serial": "meter_id", "reading_date": "date", "reading_time": "time", "kw": "kw"}

_RAW_STEP = pd.Timedelta(minutes=CONTRACT.interval_minutes)
_HALVES_PER_BUCKET = STEP // _RAW_STEP  # two 15-min readings per canonical 30-min interval


def _aggregate(group: pd.DataFrame, meter: str, findings: list | None) -> pd.DataFrame:
    """Sum 15-min kWh pairs into 30-min interval-ending buckets.

    A bucket built from only one of its two halves must NOT be silently
    summed from the survivor — a naive resample().sum() would halve the
    reading and report nothing. The lone half is scaled to the full bucket
    (assume the missing quarter-hour resembles its sibling) and flagged
    `estimated` so it is never mistaken for a measurement. `count` counts
    non-null halves, so a present-but-blank half also makes a bucket partial.
    """
    buckets = group.groupby(group["interval_end"].dt.ceil(STEP))["kwh"].agg(["sum", "count"])
    partial = buckets["count"] == 1
    for end in buckets.index[partial]:
        log.warning(
            "partial_pair_estimated",
            extra=kv(provider=CONTRACT.provider, interval_end=end, halves=1),
        )
        note(
            findings, meter, "partial_pair", Severity.WARNING,
            "30-min bucket has only one usable 15-min half",
            "scaled from the survivor, flagged estimated — not silently summed",
            end, end,
        )
    readings = pd.DataFrame(
        {
            "kwh": buckets["sum"].where(~partial, buckets["sum"] * _HALVES_PER_BUCKET),
            "flag": np.where(partial, QualityFlag.ESTIMATED, QualityFlag.ACTUAL),
        }
    )
    # A bucket with zero usable halves is no reading at all: emit nothing and
    # let to_canonical mark the interval missing.
    return readings[buckets["count"] > 0]


def load(path: Path, grid: pd.DatetimeIndex, findings: list | None = None) -> dict[str, pd.DataFrame]:
    """Read the PowerTrack file and land each meter on the canonical grid."""
    frame = read_feed_csv(path, CONTRACT, list(_COLUMNS), findings).rename(columns=_COLUMNS)

    # Day-first split columns; naive local time -> localise to the canonical
    # zone (the feed is already SAST, so no conversion, just attachment).
    ts = pd.to_datetime(
        frame["date"] + " " + frame["time"], format="%d/%m/%Y %H:%M", errors="coerce"
    )
    frame = exclude_unparseable(frame.assign(parsed=ts), ts, CONTRACT.provider, findings)

    # 1. Interval-beginning -> interval-ending BEFORE windowing or aggregation:
    #    a label names the point where its quarter-hour finishes.
    frame["interval_end"] = frame["parsed"].dt.tz_localize(grid.tz) + _RAW_STEP

    # 2. Average kW over 15 min -> kWh: energy = power x hours, 15 min = 0.25 h.
    frame["kwh"] = frame["kw"] * (CONTRACT.interval_minutes / 60)

    frame = window(frame[["meter_id", "interval_end", "kwh"]], grid, CONTRACT.provider, findings)
    frame = dedupe_last_wins(frame, CONTRACT.provider, findings)

    # 3. 15-min halves -> 30-min buckets, partial pairs flagged (see _aggregate).
    source = f"{CONTRACT.provider}:{path.name}"
    return {
        meter: to_canonical(meter, source, _aggregate(group, str(meter), findings), grid)
        for meter, group in frame.groupby("meter_id", sort=True)
    }
