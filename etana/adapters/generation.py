"""Riverside Hydro generation adapter: GEN-RH-01 onto the canonical grid.

Comma-delimited, naive SAST timestamps, 30-minute kWh — nominally already
canonical, but asserted rather than assumed. The one deviation is the interval
convention: the feed is DOCUMENTED interval-ending, yet the file spans exactly
2026-06-01 00:00 .. 2026-06-30 23:30 — under "ending" its first row would
belong to May and June's last interval would be absent. The labels are really
interval-BEGINNING, so they shift +30 min (Stage 1, D1). The data wins over
the documentation, and the override is logged so it reaches the quality report.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..canonical import STEP, QualityFlag, to_canonical
from ..config import Generator
from ..log import kv
from ..quality import Severity, note
from .base import FeedContract, dedupe_last_wins, exclude_unparseable, read_feed_csv, window

log = logging.getLogger("etana.adapters.generation")

CONTRACT = FeedContract(
    provider="RiversideHydro",
    timezone="Africa/Johannesburg",
    interval_minutes=30,
    unit="kwh",
    convention="beginning",  # documented "ending"; the raw span proves otherwise (D1)
)

_COLUMNS = {"timestamp_sast": "ts", "generation_kwh": "kwh"}


def load(
    path: Path,
    grid: pd.DatetimeIndex,
    generator: Generator,
    findings: list | None = None,
) -> dict[str, pd.DataFrame]:
    """Land the generator on the canonical grid, physically validated.

    The file carries no meter column, so identity (and nameplate) come from
    config via `generator`.
    """
    frame = read_feed_csv(path, CONTRACT, required=list(_COLUMNS)).rename(columns=_COLUMNS)
    frame["meter_id"] = generator.meter

    ts = pd.to_datetime(frame["ts"], format="%Y-%m-%d %H:%M", errors="coerce")
    frame = exclude_unparseable(frame.assign(parsed=ts), ts, CONTRACT.provider, findings)

    # D1: labels are interval-beginning despite the documentation -> +30 min
    # turns each label into the interval's end. Logged, not silent: a contract
    # override is a reportable fact about the feed, not an implementation detail.
    log.warning(
        "contract_overrides_documentation",
        extra=kv(provider=CONTRACT.provider, documented="ending", observed="beginning"),
    )
    note(
        findings, generator.meter, "contract_override", Severity.WARNING,
        "feed documented interval-ending, but the raw span (00:00..23:30, exactly "
        "the month) proves interval-beginning",
        "labels treated as beginning, shifted +30 min (D1)",
    )
    frame["interval_end"] = frame["parsed"].dt.tz_localize(grid.tz) + STEP

    frame = window(frame[["meter_id", "interval_end", "kwh"]], grid, CONTRACT.provider, findings)
    frame = dedupe_last_wins(frame, CONTRACT.provider, findings)

    # Physical bounds: 5 MW nameplate -> a 30-min interval cannot exceed
    # 5000 kW x 0.5 h = 2500 kWh, and a generator cannot consume. Violations
    # are flagged suspect with the value PRESERVED — clipping would fabricate
    # a reading; the quality report decides what to tell a human.
    cap_kwh = generator.capacity_mw * 1000 * (STEP / pd.Timedelta(hours=1))
    over, negative = frame["kwh"] > cap_kwh, frame["kwh"] < 0
    frame["flag"] = np.where(over | negative, QualityFlag.SUSPECT, QualityFlag.ACTUAL)
    log.log(
        logging.WARNING if (over | negative).any() else logging.INFO,
        "physical_bounds_check",
        extra=kv(
            provider=CONTRACT.provider,
            cap_kwh=cap_kwh,
            over_nameplate=int(over.sum()),
            negative=int(negative.sum()),
        ),
    )
    dirty = int((over | negative).sum())
    note(
        findings, generator.meter, "physical_bounds",
        Severity.WARNING if dirty else Severity.INFO,
        f"nameplate cap {cap_kwh:.0f} kWh/interval: {int(over.sum())} over, "
        f"{int(negative.sum())} negative",
        "flagged suspect, values preserved" if dirty else "none needed — within bounds",
    )

    source = f"{CONTRACT.provider}:{path.name}"
    return {
        meter: to_canonical(meter, source, group.set_index("interval_end")[["kwh", "flag"]], grid)
        for meter, group in frame.groupby("meter_id", sort=True)
    }
