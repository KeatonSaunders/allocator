"""Stage 5: kW->kWh on a known value, full-pair aggregation, a partial pair
flagged rather than silently halved, and PT-77 on the same grid as MeterFlow."""

from pathlib import Path

import pandas as pd
import pytest

from etana.adapters import meterflow, powertrack
from etana.canonical import QualityFlag, month_grid
from etana.config import load_config
from etana.log import setup

REPO = Path(__file__).resolve().parents[1]
GRID = month_grid(2026, 6, load_config(REPO / "config" / "allocation_config.json").timezone)

HEADER = "serial;reading_date;reading_time;kw"


def write_feed(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "powertrack.csv"
    path.write_bytes(("\r\n".join([HEADER, *rows])).encode())  # CRLF like the real file
    return path


def sast(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="Africa/Johannesburg")


def test_kw_to_kwh_and_full_pair_aggregation(tmp_path):
    # Beginning labels 10:00 and 10:15 cover 10:00-10:30 -> ending bucket 10:30.
    path = write_feed(tmp_path, [
        "PT-X;05/06/2026;10:00;800.0",
        "PT-X;05/06/2026;10:15;720.0",
    ])
    out = powertrack.load(path, GRID)["PT-X"]
    row = out.loc[sast("2026-06-05 10:30")]
    assert row["kwh"] == pytest.approx((800.0 + 720.0) * 0.25)  # avg kW x 0.25 h per half
    assert row["flag"] == QualityFlag.ACTUAL


def test_partial_pair_is_estimated_not_silently_halved(tmp_path, capsys):
    setup()
    path = write_feed(tmp_path, ["PT-X;05/06/2026;10:00;800.0"])  # sibling 10:15 absent
    out = powertrack.load(path, GRID)["PT-X"]
    row = out.loc[sast("2026-06-05 10:30")]
    assert row["kwh"] == pytest.approx(800.0 * 0.25 * 2)  # scaled, not the bare survivor
    assert row["flag"] == QualityFlag.ESTIMATED
    assert "partial_pair_estimated" in capsys.readouterr().err


def test_beginning_labels_shift_to_ending_at_both_month_boundaries(tmp_path):
    path = write_feed(tmp_path, [
        "PT-X;31/05/2026;23:45;999.0",  # ends 00:00 1 Jun -> belongs to May
        "PT-X;01/06/2026;00:00;800.0",  # first June quarter-hour
        "PT-X;01/06/2026;00:15;720.0",
        "PT-X;30/06/2026;23:30;600.0",  # last June half-hour
        "PT-X;30/06/2026;23:45;680.0",
    ])
    out = powertrack.load(path, GRID)["PT-X"]
    assert out.loc[GRID[0], "kwh"] == pytest.approx((800.0 + 720.0) * 0.25)
    assert out.loc[GRID[-1], "kwh"] == pytest.approx((600.0 + 680.0) * 0.25)
    assert out["kwh"].notna().sum() == 2  # the May row contributed nowhere


def test_day_first_dates_parse_as_dd_mm(tmp_path):
    path = write_feed(tmp_path, ["PT-X;13/06/2026;10:00;400.0"])  # 13th, not month 13
    out = powertrack.load(path, GRID)["PT-X"]
    assert out.loc[sast("2026-06-13 10:30"), "flag"] == QualityFlag.ESTIMATED


def test_real_file_lands_on_the_same_grid_as_meterflow():
    pt = powertrack.load(REPO / "data" / "powertrack_202606.csv", GRID)["PT-77"]
    mf = meterflow.load(REPO / "data" / "meterflow_202606.csv", GRID)["MTR-1001"]
    assert pt.index.equals(mf.index)
    # Stage 1 recon: the two absent 15-min rows are BOTH halves of the bucket
    # ending 14:00 on 17 June -> one whole missing interval, no partial pairs.
    assert (pt["flag"] == QualityFlag.MISSING).sum() == 1
    assert pt["kwh"].isna().sum() == 1
    assert pd.isna(pt.loc[sast("2026-06-17 14:00"), "kwh"])
    assert (pt["flag"] == QualityFlag.ESTIMATED).sum() == 0
    # First bucket: (715.216 + 803.397) avg kW x 0.25 h.
    assert pt.loc[GRID[0], "kwh"] == pytest.approx((715.216 + 803.397) * 0.25)
