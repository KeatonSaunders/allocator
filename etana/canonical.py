"""The one interval grid every feed must land on.

Canonical form: 30-minute intervals, kWh, tz-aware SAST, interval-ENDING.
Adapters do whatever their provider requires (UTC, kW, 15-min,
interval-beginning) and hand this module readings already in canonical form;
from here on the pipeline speaks one language only: a DataFrame indexed by
`interval_end` with columns `meter_id, kwh, flag, source`.
"""

from __future__ import annotations

from enum import StrEnum
from zoneinfo import ZoneInfo

import pandas as pd

# Canonical by definition — config decides *which* month and zone, not what
# the canonical shape is.
STEP = pd.Timedelta(minutes=30)


# StrEnum (not str+Enum): members stringify to their value, so they compare
# elementwise against pandas 3.0 str-dtype columns.
class QualityFlag(StrEnum):
    ACTUAL = "actual"                # as received from the provider
    INTERPOLATED = "interpolated"    # short gap, linearly filled (quality engine)
    ESTIMATED = "estimated"          # longer gap, profile-estimated (quality engine)
    SUSPECT = "suspect"              # present but not trusted, or a refused fill
    MISSING = "missing"              # no reading; not yet assessed by the quality engine


def month_grid(year: int, month: int, tz: ZoneInfo) -> pd.DatetimeIndex:
    """Interval-ending labels for one billing month.

    An interval-ending label names the point where the half-hour *finishes*,
    so the label 00:00 on the 1st covers 23:30-24:00 of the PREVIOUS month and
    is excluded; the month's first label is 00:30 on the 1st (covering
    00:00-00:30) and its last is 00:00 on the 1st of the next month (covering
    the final half-hour of the last day). June 2026 -> 1440 labels,
    2026-06-01 00:30 .. 2026-07-01 00:00 SAST.

    South Africa observes no DST, so no fold/gap handling is required — the
    absence of DST logic here is a recorded conclusion, not an omission.
    """
    start = pd.Timestamp(year=year, month=month, day=1, tz=tz)
    end = pd.Timestamp(year=year + month // 12, month=month % 12 + 1, day=1, tz=tz)
    return pd.date_range(start + STEP, end, freq=STEP, name="interval_end")


def to_canonical(
    meter_id: str,
    source: str,
    readings: pd.DataFrame,
    grid: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Land one meter's readings on the grid, one row per grid label.

    `readings`: column `kwh` (canonical units/convention already applied),
    optional column `flag` for values the adapter itself distrusts, indexed by
    tz-aware interval_end.

    A grid label without a reading becomes an explicit MISSING row (kwh=NaN) —
    never dropped, never zero-filled; the quality engine decides its fate.
    Readings off the grid or duplicated labels are programming errors here
    (adapters window, dedupe and align *before* landing), so they fail loud.
    """
    if readings.index.has_duplicates:
        dup = readings.index[readings.index.duplicated()][0]
        raise ValueError(
            f"{meter_id}: duplicate interval labels (e.g. {dup}) must be "
            f"resolved before landing on the grid"
        )
    stray = readings.index.difference(grid)
    if not stray.empty:
        raise ValueError(
            f"{meter_id}: {len(stray)} reading(s) off the canonical grid "
            f"(e.g. {stray[0]}) — adapter must window and align first"
        )

    out = readings.reindex(grid)
    present = grid.isin(readings.index)
    flags = out.get("flag", pd.Series(index=grid, dtype=object))
    flags = flags.fillna(QualityFlag.ACTUAL.value)     # adapter default: trusted
    out["flag"] = flags.where(present, QualityFlag.MISSING.value)
    out["kwh"] = out["kwh"].astype("float64")
    out.insert(0, "meter_id", meter_id)
    out["source"] = source
    return out[["meter_id", "kwh", "flag", "source"]]
