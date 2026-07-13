"""Stage 7: exact vs conflicting duplicates reported differently; short gaps
interpolate, medium gaps profile-estimate, long gaps are refused; missing
intervals appear in the completeness reconciliation; zero/frozen runs flagged."""

from pathlib import Path

import pandas as pd
import pytest

from etana.adapters import generation, meterflow, powertrack
from etana.adapters.base import dedupe_last_wins
from etana.canonical import QualityFlag, month_grid, to_canonical
from etana.config import load_config
from etana.quality import Severity, assess

REPO = Path(__file__).resolve().parents[1]
CFG = load_config(REPO / "config" / "allocation_config.json")
GRID = month_grid(2026, 6, CFG.timezone)


def sast(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="Africa/Johannesburg")


def feed_with_holes(holes: list[pd.Timestamp]) -> pd.DataFrame:
    """A full canonical feed whose kwh encodes weekday and slot — so the
    profile mean for any (weekday, slot) equals that same encoding, making
    estimates exactly predictable — with the given labels left missing."""
    kwh = pd.Series(GRID.weekday * 100.0 + GRID.hour + GRID.minute / 60, index=GRID)
    readings = kwh.drop(holes).to_frame("kwh")
    return to_canonical("MTR-T", "test", readings, GRID)


def by_check(findings: list, check: str) -> list:
    return [f for f in findings if f.check == check]


def test_exact_vs_conflicting_duplicates_reported_differently():
    t1, t2 = GRID[10], GRID[20]
    frame = pd.DataFrame(
        {
            "meter_id": ["M"] * 4,
            "interval_end": [t1, t1, t2, t2],
            "kwh": [5.0, 5.0, 3.0, 7.0],  # exact pair, then conflicting pair
        }
    )
    findings: list = []
    clean = dedupe_last_wins(frame, "TestProvider", findings)
    assert clean.set_index("interval_end")["kwh"].to_dict() == {t1: 5.0, t2: 7.0}
    dups = by_check(findings, "duplicates")
    exact = [f for f in dups if f.severity == Severity.INFO]
    conflict = [f for f in dups if f.severity == Severity.WARNING]
    assert len(exact) == 1 and "1 exact" in exact[0].detail
    assert len(conflict) == 1  # every conflict individually, with both values
    assert "3.0" in conflict[0].detail and "7.0" in conflict[0].detail
    assert "7.0" in conflict[0].action  # and the tie-break stated


def test_short_gap_interpolates():
    holes = [sast("2026-06-10 09:30"), sast("2026-06-10 10:00")]
    findings: list = []
    out = assess(feed_with_holes(holes), findings)
    # Linear between anchors 09:00 (=2*100 + 9.0) and 10:30 (=210.5):
    # 1.5 kWh over three steps -> 0.5 per half-hour.
    assert out.loc[holes[0], "kwh"] == pytest.approx(209.5)
    assert out.loc[holes[1], "kwh"] == pytest.approx(210.0)
    assert (out.loc[holes, "flag"] == QualityFlag.INTERPOLATED).all()
    gap = by_check(findings, "gap")
    assert len(gap) == 1 and gap[0].severity == Severity.WARNING


def test_medium_gap_profile_estimated_not_mean_substituted():
    holes = list(pd.date_range(sast("2026-06-10 09:30"), periods=6, freq="30min"))
    out = assess(feed_with_holes(holes))
    # Same weekday x same slot elsewhere in June carries the identical
    # encoding, so the profile estimate must reproduce it exactly.
    expected = [t.weekday() * 100.0 + t.hour + t.minute / 60 for t in holes]
    assert list(out.loc[holes, "kwh"]) == pytest.approx(expected)
    assert (out.loc[holes, "flag"] == QualityFlag.ESTIMATED).all()


def test_long_gap_refused_and_escalated():
    holes = list(pd.date_range(sast("2026-06-10 06:00"), periods=13, freq="30min"))
    findings: list = []
    out = assess(feed_with_holes(holes), findings)
    assert out.loc[holes, "kwh"].isna().all()  # nothing invented
    assert (out.loc[holes, "flag"] == QualityFlag.SUSPECT).all()
    gap = by_check(findings, "gap")
    assert len(gap) == 1 and gap[0].severity == Severity.ERROR
    assert "REFUSED" in gap[0].action


def test_completeness_reconciliation_reports_missing_intervals():
    findings: list = []
    assess(feed_with_holes([sast("2026-06-10 09:30")]), findings)
    completeness = by_check(findings, "completeness")[0]
    assert completeness.severity == Severity.WARNING
    assert "1439 measured" in completeness.detail and "1 missing" in completeness.detail
    boundary = by_check(findings, "month_boundary")[0]
    assert boundary.severity == Severity.INFO  # both edges present


def test_zero_and_frozen_runs_flagged_suspect_with_values_kept():
    feed = feed_with_holes([])
    zeros = pd.date_range(sast("2026-06-08 10:30"), periods=8, freq="30min")
    frozen = pd.date_range(sast("2026-06-20 10:30"), periods=7, freq="30min")
    feed.loc[zeros, "kwh"] = 0.0
    feed.loc[frozen, "kwh"] = 42.0
    findings: list = []
    out = assess(feed, findings)
    assert (out.loc[zeros, "flag"] == QualityFlag.SUSPECT).all()
    assert (out.loc[frozen, "flag"] == QualityFlag.SUSPECT).all()
    assert (out.loc[frozen, "kwh"] == 42.0).all()  # preserved
    assert len(by_check(findings, "zero_run")) == 1
    assert len(by_check(findings, "frozen_reading")) == 1


def test_real_data_end_to_end_quality():
    findings: list = []
    mf = meterflow.load(REPO / "data" / "meterflow_202606.csv", GRID, findings)
    pt = powertrack.load(REPO / "data" / "powertrack_202606.csv", GRID, findings)
    gen = generation.load(
        REPO / "data" / "riverside_hydro_generation_202606.csv", GRID, CFG.generator, findings
    )
    assessed = {m: assess(f, findings) for m, f in (mf | pt | gen).items()}

    # MTR-1001's 12-interval Friday gap sits exactly at the estimate threshold.
    assert (assessed["MTR-1001"]["flag"] == QualityFlag.ESTIMATED).sum() == 12
    assert assessed["MTR-1001"]["kwh"].notna().all()
    # PT-77: one whole missing interval interpolates; the 5h frozen run
    # (352 kW -> 176 kWh x 10 canonical intervals) goes suspect.
    assert (assessed["PT-77"]["flag"] == QualityFlag.INTERPOLATED).sum() == 1
    assert (assessed["PT-77"]["flag"] == QualityFlag.SUSPECT).sum() == 10
    # Generation: the 12h zero run goes suspect, values preserved as zeros.
    gen_frame = assessed["GEN-RH-01"]
    assert (gen_frame["flag"] == QualityFlag.SUSPECT).sum() == 24
    assert (gen_frame.loc[gen_frame["flag"] == QualityFlag.SUSPECT, "kwh"] == 0).all()

    # The Stage 1 quirk log, as structured findings.
    assert any(f.check == "duplicates" and f.severity == Severity.WARNING for f in findings)
    assert any(f.check == "contract_override" for f in findings)
    assert any(f.check == "frozen_reading" and f.meter == "PT-77" for f in findings)
    assert any(f.check == "zero_run" and f.meter == "GEN-RH-01" for f in findings)
    # And passed checks are recorded as evidence, not just failures.
    assert any(f.severity == Severity.INFO for f in findings)
    assert not any(f.check == "gap" and f.severity == Severity.ERROR for f in findings)
