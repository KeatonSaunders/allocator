"""Stage 6: over-nameplate and negative values are flagged suspect with their
values preserved (never clipped or dropped), and the documented-vs-actual
interval convention override lands the feed on the right June intervals."""

from pathlib import Path

import pandas as pd
import pytest

from etana.adapters.generation import load
from etana.canonical import QualityFlag, month_grid
from etana.config import load_config

REPO = Path(__file__).resolve().parents[1]
CFG = load_config(REPO / "config" / "allocation_config.json")
GRID = month_grid(2026, 6, CFG.timezone)

HEADER = "timestamp_sast,generation_kwh"


def write_feed(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "generation.csv"
    path.write_bytes(("\r\n".join([HEADER, *rows])).encode())  # CRLF like the real file
    return path


def test_over_nameplate_is_flagged_suspect_not_clipped(tmp_path):
    # 5 MW nameplate -> 2500 kWh max per half-hour; 2600 is impossible.
    path = write_feed(tmp_path, ["2026-06-05 10:00,2600.0"])
    out = load(path, GRID, CFG.generator)[CFG.generator.meter]
    row = out.loc[pd.Timestamp("2026-06-05 10:30", tz="Africa/Johannesburg")]
    assert row["flag"] == QualityFlag.SUSPECT
    assert row["kwh"] == 2600.0  # preserved, not clipped to 2500


def test_negative_generation_is_flagged_suspect(tmp_path):
    path = write_feed(tmp_path, ["2026-06-05 10:00,-5.0"])
    out = load(path, GRID, CFG.generator)[CFG.generator.meter]
    row = out.loc[pd.Timestamp("2026-06-05 10:30", tz="Africa/Johannesburg")]
    assert row["flag"] == QualityFlag.SUSPECT
    assert row["kwh"] == -5.0  # preserved, not zeroed or dropped


def test_beginning_labels_shift_to_ending_across_the_month(tmp_path):
    path = write_feed(tmp_path, [
        "2026-05-31 23:30,111.0",  # ends 00:00 1 Jun -> belongs to May
        "2026-06-01 00:00,222.0",  # June's first interval
        "2026-06-30 23:30,333.0",  # June's last interval
    ])
    out = load(path, GRID, CFG.generator)[CFG.generator.meter]
    assert out.loc[GRID[0], "kwh"] == 222.0
    assert out.loc[GRID[-1], "kwh"] == 333.0
    assert out["kwh"].notna().sum() == 2  # the May row excluded


def test_real_file_is_complete_within_bounds_and_zeros_preserved():
    out = load(REPO / "data" / "riverside_hydro_generation_202606.csv", GRID, CFG.generator)
    frame = out[CFG.generator.meter]
    assert frame.index.equals(GRID)
    assert (frame["flag"] == QualityFlag.MISSING).sum() == 0
    assert (frame["flag"] == QualityFlag.SUSPECT).sum() == 0  # max 2382.348 < 2500, no negatives
    # The 15 June outage: 24 exact zeros survive as ACTUAL values — whether
    # they are an outage or missing-coded-as-zero is Stage 7's finding to raise.
    assert (frame["kwh"] == 0).sum() == 24
    assert frame.loc[pd.Timestamp("2026-06-15 12:00", tz="Africa/Johannesburg"), "kwh"] == 0.0
