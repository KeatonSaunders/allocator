"""Data quality engine: the standing checks every canonical feed must pass.

Checks produce QualityFinding records — failures AND passes, because a check
that ran and held is evidence while an unstated assumption is not. Raw adapter
output stays immutable: assess() returns a NEW frame with the gap policy
applied and flags updated, so billed totals can be split by data quality all
the way through the reporting layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from .canonical import QualityFlag
from .log import kv

log = logging.getLogger("etana.quality")

# Gap policy on canonical 30-min intervals (justified one line each, README):
INTERPOLATE_MAX = 2  # <= 1 h: two neighbours constrain a line tightly enough to bill on
ESTIMATE_MAX = 12    # <= 6 h: a same-weekday, same-slot profile is still meaningful
# > ESTIMATE_MAX: invent nothing — leave null, flag suspect, escalate.

# >= 3 h of byte-identical readings: real interval meters jitter, so exact
# repeats this long point at a stuck register or zero-coded outage.
RUN_MIN = 6


class Severity(StrEnum):
    INFO = "info"        # a check that ran and passed, or a neutral observation
    WARNING = "warning"  # a quirk found and handled; the handling is stated
    ERROR = "error"      # escalation: no automatic handling is defensible


@dataclass(frozen=True)
class QualityFinding:
    meter: str  # meter id, or provider name for feed-level findings
    check: str
    severity: Severity
    detail: str
    action: str
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None


def note(
    findings: list[QualityFinding] | None,
    meter: str,
    check: str,
    severity: Severity,
    detail: str,
    action: str,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> None:
    """Record a finding when a collector was supplied (adapters also run in
    tests without one); logging stays at the point of discovery."""
    if findings is not None:
        findings.append(QualityFinding(meter, check, severity, detail, action, start, end))


def _contiguous(member: pd.Series):
    """Yield the index of each contiguous run of True in a boolean series."""
    run_id = (member != member.shift()).cumsum()
    for _, span in member[member].groupby(run_id[member]):
        yield span.index


def assess(feed: pd.DataFrame, findings: list[QualityFinding] | None = None) -> pd.DataFrame:
    """Run the standing checks over one canonical feed.

    Order matters: value-run checks look only at measured (actual) rows, so
    they run before gap filling can add synthetic values; gap estimation in
    turn builds its profile only from rows still trusted after the run checks.
    """
    feed = feed.copy()  # raw adapter output stays immutable (provenance)
    meter = str(feed["meter_id"].iloc[0])
    _completeness(feed, meter, findings)
    _value_runs(feed, meter, findings)
    _gaps(feed, meter, findings)

    # Reconciliation invariants, asserted on every execution:
    counts = feed["flag"].value_counts()
    assert counts.get(QualityFlag.MISSING, 0) == 0, "assess left an interval unassessed"
    assert int(counts.sum()) == len(feed), "flag counts do not reconcile with the grid"
    log.info(
        "assessed",
        extra=kv(meter=meter, **{str(flag): int(n) for flag, n in counts.items()}),
    )
    return feed


def _completeness(feed: pd.DataFrame, meter: str, findings) -> None:
    """Expected vs actual interval counts; missing rows and present-but-null
    values are different problems and are reported separately."""
    missing = int((feed["flag"] == QualityFlag.MISSING).sum())
    present_null = int((feed["kwh"].isna() & (feed["flag"] != QualityFlag.MISSING)).sum())
    measured = len(feed) - missing - present_null
    note(
        findings, meter, "completeness",
        Severity.INFO if missing + present_null == 0 else Severity.WARNING,
        f"{len(feed)} intervals expected: {measured} measured, "
        f"{missing} missing rows, {present_null} present-but-null",
        "reported", feed.index[0], feed.index[-1],
    )
    # The month's first and last interval, explicitly, after tz conversion —
    # the place boundary bugs land.
    edges = {"first": feed.iloc[0], "last": feed.iloc[-1]}
    absent = [name for name, row in edges.items() if pd.isna(row["kwh"])]
    note(
        findings, meter, "month_boundary",
        Severity.WARNING if absent else Severity.INFO,
        f"boundary intervals {'missing: ' + ', '.join(absent) if absent else 'both present'}",
        "reported",
    )


def _value_runs(feed: pd.DataFrame, meter: str, findings) -> None:
    """Runs of identical measured values: zeros (outage, fault, or
    missing-coded-as-zero) and frozen readings. Flagged suspect with values
    preserved; the cause is never assumed."""
    values = feed["kwh"].where(feed["flag"] == QualityFlag.ACTUAL)
    run_id = values.ne(values.shift()).cumsum()  # NaN != NaN, so gaps break runs
    found = 0
    for _, run in values.dropna().groupby(run_id):
        if len(run) < RUN_MIN:
            continue
        found += 1
        check = "zero_run" if run.iloc[0] == 0 else "frozen_reading"
        feed.loc[run.index, "flag"] = QualityFlag.SUSPECT
        note(
            findings, meter, check, Severity.WARNING,
            f"value {run.iloc[0]} repeated for {len(run)} intervals — "
            f"outage, stuck register or missing-coded-as-zero; cause not assumed",
            "flagged suspect, values preserved", run.index[0], run.index[-1],
        )
        log.warning("value_run", extra=kv(meter=meter, check=check, length=len(run), value=run.iloc[0]))
    if not found:
        note(findings, meter, "value_runs", Severity.INFO,
             f"no zero or frozen runs of {RUN_MIN}+ intervals", "none needed")


def _gaps(feed: pd.DataFrame, meter: str, findings) -> None:
    """Classify contiguous null runs by length; never mean-substitute.

    <= INTERPOLATE_MAX: linear interpolation — neighbours constrain it.
    <= ESTIMATE_MAX: profile estimate, the mean of this meter's trusted
       readings in the same weekday x same half-hour slot (finer than a TOU-
       period mean and TOU-consistent by construction, since every slot lies
       in exactly one TOU bucket).
    beyond, or when anchors/profile are unavailable: refuse — no data
       invented; null stays, flagged suspect, escalated, excluded from billed
       totals with the exclusion made visible.
    """
    holes = feed["kwh"].isna()
    if not holes.any():
        note(findings, meter, "gaps", Severity.INFO, "no gaps after landing", "none needed")
        return

    trusted = feed[feed["flag"] == QualityFlag.ACTUAL]
    profile = trusted.groupby([trusted.index.weekday, trusted.index.time])["kwh"].mean()
    interpolated = feed["kwh"].interpolate(method="time", limit_area="inside")

    for span in _contiguous(holes):
        n = len(span)
        estimate = pd.Series(
            [profile.get((t.weekday(), t.time()), np.nan) for t in span],
            index=span, dtype="float64",
        )
        if n <= INTERPOLATE_MAX and interpolated[span].notna().all():
            feed.loc[span, "kwh"] = interpolated[span]
            feed.loc[span, "flag"] = QualityFlag.INTERPOLATED
            action = "linear interpolation, flagged interpolated"
        elif n <= ESTIMATE_MAX and estimate.notna().all():
            feed.loc[span, "kwh"] = estimate
            feed.loc[span, "flag"] = QualityFlag.ESTIMATED
            action = "weekday x slot profile estimate, flagged estimated"
        else:
            feed.loc[span, "flag"] = QualityFlag.SUSPECT
            action = "REFUSED: no data invented; excluded from billed totals"
        severity = Severity.ERROR if action.startswith("REFUSED") else Severity.WARNING
        note(findings, meter, "gap", severity, f"{n}-interval gap", action, span[0], span[-1])
        log.log(
            logging.ERROR if severity == Severity.ERROR else logging.WARNING,
            "gap", extra=kv(meter=meter, intervals=n, start=span[0], action=action.split(",")[0]),
        )
