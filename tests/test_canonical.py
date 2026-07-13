"""Stage 3: the canonical June 2026 grid is exactly right at both boundaries,
and landing on it marks missing intervals instead of dropping or zero-filling."""

from pathlib import Path

import pandas as pd
import pytest

from etana.canonical import STEP, QualityFlag, month_grid, to_canonical
from etana.config import load_config

REPO = Path(__file__).resolve().parents[1]
TZ = load_config(REPO / "config" / "allocation_config.json").timezone


def test_june_grid_is_exactly_1440_interval_ending_labels():
    grid = month_grid(2026, 6, TZ)
    assert len(grid) == 1440
    # The interval ending midnight 1 June belongs to May; the one ending
    # midnight 1 July is June's last.
    assert grid[0] == pd.Timestamp("2026-06-01 00:30", tz="Africa/Johannesburg")
    assert grid[-1] == pd.Timestamp("2026-07-01 00:00", tz="Africa/Johannesburg")


def test_grid_strictly_monotonic_nothing_skipped_or_duplicated():
    grid = month_grid(2026, 6, TZ)
    assert grid.is_unique and grid.is_monotonic_increasing
    assert (pd.Series(grid).diff().dropna() == STEP).all()


def test_grid_handles_year_rollover():
    grid = month_grid(2026, 12, TZ)
    assert len(grid) == 31 * 48
    assert grid[-1] == pd.Timestamp("2027-01-01 00:00", tz="Africa/Johannesburg")


def test_to_canonical_marks_missing_rather_than_dropping():
    grid = month_grid(2026, 6, TZ)
    readings = pd.DataFrame({"kwh": 1.5}, index=grid.delete(7))
    out = to_canonical("MTR-X", "test", readings, grid)
    assert len(out) == 1440                       # nothing dropped
    assert (out.index == grid).all()
    missing = out[out["flag"] == QualityFlag.MISSING]
    assert list(missing.index) == [grid[7]]
    assert missing["kwh"].isna().all()            # not zero-filled
    assert (out.drop(grid[7])["flag"] == QualityFlag.ACTUAL).all()


def test_to_canonical_preserves_adapter_flags():
    grid = month_grid(2026, 6, TZ)
    readings = pd.DataFrame(
        {"kwh": 1.0, "flag": QualityFlag.ACTUAL.value}, index=grid
    )
    readings.loc[grid[3], "flag"] = QualityFlag.SUSPECT.value
    out = to_canonical("MTR-X", "test", readings, grid)
    assert out.loc[grid[3], "flag"] == QualityFlag.SUSPECT
    assert (out["flag"] == QualityFlag.SUSPECT).sum() == 1


def test_to_canonical_rejects_off_grid_labels():
    grid = month_grid(2026, 6, TZ)
    readings = pd.DataFrame(
        {"kwh": 1.0}, index=pd.DatetimeIndex([grid[0] + pd.Timedelta(minutes=15)])
    )
    with pytest.raises(ValueError, match="off the canonical grid"):
        to_canonical("MTR-X", "test", readings, grid)


def test_to_canonical_rejects_unresolved_duplicate_labels():
    grid = month_grid(2026, 6, TZ)
    readings = pd.DataFrame({"kwh": 1.0}, index=grid[:2].append(grid[:1]))
    with pytest.raises(ValueError, match="duplicate"):
        to_canonical("MTR-X", "test", readings, grid)
