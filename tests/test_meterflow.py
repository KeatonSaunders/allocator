"""Stage 4: UTC->SAST conversion happens before windowing (no dropped boundary
hours), exclusions are logged, and both stacked meters land on the grid."""

from pathlib import Path

import pandas as pd
import pytest

from etana.adapters.meterflow import load
from etana.canonical import QualityFlag, month_grid
from etana.config import load_config
from etana.log import setup

REPO = Path(__file__).resolve().parents[1]
GRID = month_grid(2026, 6, load_config(REPO / "config" / "allocation_config.json").timezone)

HEADER = "meter_serial,reading_timestamp_utc,consumption_kwh"


def write_feed(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "meterflow.csv"
    path.write_bytes(("\r\n".join([HEADER, *rows])).encode())  # CRLF like the real file
    return path


def test_month_boundary_utc_rows_convert_and_survive_window(tmp_path):
    path = write_feed(tmp_path, [
        "M-1,2026-05-31T22:00:00Z,111.0",  # ends 00:00 SAST 1 Jun -> belongs to May
        "M-1,2026-05-31T22:30:00Z,222.0",  # June's first interval
        "M-1,2026-06-30T22:00:00Z,333.0",  # June's last interval
        "M-1,2026-06-30T22:30:00Z,444.0",  # July
    ])
    out = load(path, GRID)["M-1"]
    assert out.loc[GRID[0], "kwh"] == 222.0   # boundary hour kept, not clipped
    assert out.loc[GRID[-1], "kwh"] == 333.0
    assert out["kwh"].notna().sum() == 2      # May/July rows excluded
    assert not out["kwh"].isin([111.0, 444.0]).any()


def test_out_of_window_exclusions_are_logged(tmp_path, capsys):
    setup()
    path = write_feed(tmp_path, [
        "M-1,2026-05-31T22:00:00Z,111.0",
        "M-1,2026-06-01T10:00:00Z,222.0",
        "M-1,2026-06-30T22:30:00Z,444.0",
    ])
    load(path, GRID)
    err = capsys.readouterr().err
    assert "rows_excluded_outside_window" in err
    assert "count=2" in err


def test_stacked_meters_split_by_id_not_row_order(tmp_path):
    path = write_feed(tmp_path, [
        "M-B,2026-06-01T10:00:00Z,1.0",  # interleaved and unsorted on purpose
        "M-A,2026-06-01T10:30:00Z,2.0",
        "M-B,2026-06-01T09:00:00Z,3.0",
        "M-A,2026-06-01T10:00:00Z,4.0",
    ])
    out = load(path, GRID)
    assert set(out) == {"M-A", "M-B"}
    noon = pd.Timestamp("2026-06-01 12:00", tz="Africa/Johannesburg")
    assert out["M-A"].loc[noon, "kwh"] == 4.0
    assert out["M-B"].loc[noon, "kwh"] == 1.0
    assert len(out["M-A"]) == len(GRID) and len(out["M-B"]) == len(GRID)


def test_real_file_lands_both_meters_on_the_canonical_grid():
    out = load(REPO / "data" / "meterflow_202606.csv", GRID)
    assert set(out) == {"MTR-1001", "MTR-1002"}
    for frame in out.values():
        assert (frame.index == GRID).all()
    # Stage 1 recon: MTR-1001 has a contiguous 12-interval gap; MTR-1002 is
    # complete once its five duplicate rows collapse.
    assert (out["MTR-1001"]["flag"] == QualityFlag.MISSING).sum() == 12
    assert (out["MTR-1002"]["flag"] == QualityFlag.MISSING).sum() == 0


def test_conflicting_duplicate_keeps_last_received_and_logs_both(capsys):
    setup()
    out = load(REPO / "data" / "meterflow_202606.csv", GRID)
    # 2026-06-08T08:30Z arrived twice with different values; last wins.
    label = pd.Timestamp("2026-06-08 10:30", tz="Africa/Johannesburg")
    assert out["MTR-1002"].loc[label, "kwh"] == 1140.161
    err = capsys.readouterr().err
    assert "conflicting_duplicate" in err
    assert "633.423" in err and "1140.161" in err  # both values logged
    assert "exact_duplicates=4" in err and "conflicts=1" in err
