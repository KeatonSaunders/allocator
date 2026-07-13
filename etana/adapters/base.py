"""Shared adapter machinery.

Provider quirks (delimiters, units, timezones, conventions) live in the
adapter modules and nowhere else — a new provider is a new adapter, not a
patch to the engine. This module holds what every adapter needs: the
FeedContract it must declare, fail-loud FeedError for structural problems,
month windowing, and last-received-wins dedupe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..canonical import STEP
from ..log import kv
from ..quality import QualityFinding, Severity, note

log = logging.getLogger("etana.adapters")

KEY = ["meter_id", "interval_end"]


class FeedError(Exception):
    """Structural problem with a feed file (missing, unreadable, wrong schema):
    fail loud and early. Data problems are never raised — they are reported."""


@dataclass(frozen=True)
class FeedContract:
    """What we assert about a provider's file before trusting a single row.
    Established in Stage 1 recon (see DECISIONS.md) and enforced by the adapter
    that declares it; the data quality report prints it as asserted."""

    provider: str
    timezone: str          # zone of the raw timestamps
    interval_minutes: int  # raw interval length
    unit: str              # "kwh" (energy) or "kw" (average power over the interval)
    convention: str        # raw labels: "ending" or "beginning"
    register: str = "interval"  # vs "cumulative", which would need differencing
    sign: str = "positive"      # negatives physically impossible for these feeds
    delimiter: str = ","
    decimal: str = "."


def read_feed_csv(
    path: Path,
    contract: FeedContract,
    required: list[str],
    findings: list[QualityFinding] | None = None,
) -> pd.DataFrame:
    """Read a provider file in its declared format; wrong shape fails loud."""
    try:
        frame = pd.read_csv(
            path, sep=contract.delimiter, decimal=contract.decimal, encoding="utf-8-sig"
        )
    except FileNotFoundError:
        raise FeedError(f"{contract.provider}: file not found: {path}") from None
    except pd.errors.ParserError as exc:
        raise FeedError(f"{contract.provider}: unparseable CSV {path}: {exc}") from None
    missing = set(required) - set(frame.columns)
    if missing:
        raise FeedError(
            f"{contract.provider}: {path} lacks expected column(s) "
            f"{sorted(missing)}; found {list(frame.columns)}"
        )
    log.info("feed_read", extra=kv(provider=contract.provider, file=path.name, rows=len(frame)))
    note(findings, contract.provider, "feed_read", Severity.INFO,
         f"{len(frame)} data rows read from {path.name}", "parsed against contract")
    return frame


def window(
    frame: pd.DataFrame,
    grid: pd.DatetimeIndex,
    provider: str,
    findings: list[QualityFinding] | None = None,
) -> pd.DataFrame:
    """Keep rows whose interval_end lies inside the billing month.

    Callers convert to the canonical zone *before* windowing — the SAST June
    month starts at 22:00 UTC on 31 May, so clipping on raw labels would lose
    or gain boundary hours. The span is half-open (month_start, month_end] on
    ending labels, so it also admits sub-canonical labels (a 15-min feed's
    first June label ends 00:15, before grid[0]). Excluded rows are counted
    and logged, not silently dropped.
    """
    month_start = grid[0] - STEP
    inside = (frame["interval_end"] > month_start) & (frame["interval_end"] <= grid[-1])
    excluded = int((~inside).sum())
    if excluded:
        log.info(
            "rows_excluded_outside_window",
            extra=kv(provider=provider, count=excluded),
        )
        note(
            findings, provider, "window", Severity.INFO,
            f"{excluded} row(s) outside the billing month after tz conversion",
            "excluded from the month, counted",
        )
    return frame[inside]


def exclude_unparseable(
    frame: pd.DataFrame,
    ts: pd.Series,
    provider: str,
    findings: list[QualityFinding] | None = None,
) -> pd.DataFrame:
    """Drop rows whose timestamp failed to parse — loudly, row by row. A
    garbage timestamp cannot be keyed to any interval, so exclusion is the
    only honest option; silence is not."""
    bad = frame[ts.isna()]
    for _, row in bad.iterrows():
        log.warning("unparseable_timestamp", extra=kv(provider=provider, row=dict(row)))
    if len(bad):
        note(
            findings, provider, "unparseable_timestamp", Severity.WARNING,
            f"{len(bad)} row(s) with unparseable timestamps (each logged in full)",
            "excluded — cannot be keyed to any interval",
        )
    return frame[ts.notna()]


def dedupe_last_wins(
    frame: pd.DataFrame,
    provider: str,
    findings: list[QualityFinding] | None = None,
) -> pd.DataFrame:
    """Collapse re-sent readings on (meter_id, interval_end).

    Exact duplicates (same key, same value) collapse safely. Conflicting ones
    (same key, different value) keep the LAST received — providers re-send
    corrections, and file order is arrival order — with every conflict logged
    individually with both values. A bare drop_duplicates() would keep one of
    a conflicting pair and report nothing.
    """
    dup = frame[frame.duplicated(KEY, keep=False)]
    if dup.empty:
        note(findings, provider, "duplicates", Severity.INFO,
             "no duplicate (meter, interval) keys", "none needed")
        return frame
    value_counts = dup.groupby(KEY)["kwh"].transform("nunique")
    exact = dup[value_counts == 1].drop_duplicates(KEY)
    conflicts = dup[value_counts > 1]
    if len(exact):
        note(findings, provider, "duplicates", Severity.INFO,
             f"{len(exact)} exact duplicate key(s) (same value re-sent)",
             "collapsed — identical, no tie-break needed")
    for (meter, end), group in conflicts.groupby(KEY):
        log.warning(
            "conflicting_duplicate",
            extra=kv(
                provider=provider,
                meter=meter,
                interval_end=end,
                values=list(group["kwh"]),
                kept=group["kwh"].iloc[-1],
            ),
        )
        note(
            findings, str(meter), "duplicates", Severity.WARNING,
            f"conflicting duplicate: values {list(group['kwh'])} for the same interval",
            f"kept last-received {group['kwh'].iloc[-1]} (file order = arrival order)",
            end, end,
        )
    clean = frame.drop_duplicates(KEY, keep="last")
    log.info(
        "dedupe",
        extra=kv(
            provider=provider,
            rows_in=len(frame),
            exact_duplicates=len(exact),
            conflicts=len(conflicts.drop_duplicates(KEY)),
            rows_out=len(clean),
        ),
    )
    return clean
