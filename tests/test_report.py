"""Stage 10: one CLI command produces both deliverables; the reports carry the
contracts, the findings with actions, the passed checks, the billing basis and
the by-flag split; ZAR is rounded exactly once."""

from pathlib import Path

import pandas as pd
import pytest

from etana.cli import main

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("out")
    code = main([
        "run",
        "--config", str(REPO / "config" / "allocation_config.json"),
        "--data", str(REPO / "data"),
        "--out", str(out),
    ])
    assert code == 0
    return out


def test_one_command_writes_both_deliverables(out_dir):
    names = {p.name for p in out_dir.iterdir()}
    assert names == {"quality_report.md", "findings.csv", "monthly_summary.md", "monthly_summary.csv"}


def test_quality_report_carries_contracts_findings_and_passes(out_dir):
    text = (out_dir / "quality_report.md").read_text(encoding="utf-8")
    assert "Feed contracts" in text and "PowerTrack" in text and "beginning" in text
    # The Stage 1 quirks, each with an action stated.
    assert "conflicting duplicate" in text and "last-received" in text
    assert "contract_override" in text
    assert "zero_run" in text and "frozen_reading" in text
    assert "profile estimate" in text  # the 12-interval gap's action
    assert "Checks that ran and passed" in text
    findings = pd.read_csv(out_dir / "findings.csv")
    assert set(findings["severity"]) >= {"info", "warning"}
    assert not (findings["severity"] == "error").any()  # no refusals this month


def test_summary_states_billing_basis_and_flag_split(out_dir):
    text = (out_dir / "monthly_summary.md").read_text(encoding="utf-8")
    assert "ALLOCATED energy × TOU rate" in text
    assert "Atlantic Foods (MTR-1001)" in text
    assert "Billed (allocated) energy by data quality flag" in text
    assert "unallocated_pct" in text


def test_summary_csv_full_precision_and_zar_rounded_once(out_dir):
    table = pd.read_csv(out_dir / "monthly_summary.csv")
    assert (table["wheeling_zar"] == table["allocated_kwh"] * table["rate_zar_per_kwh"]).all()
    assert (table["wheeling_zar_rounded"] == table["wheeling_zar"].round(2)).all()
    assert table["excluded_intervals"].sum() == 0  # all gaps filled this month
    assert len(table) == 9  # 3 sites x 3 TOU buckets


def test_missing_feed_file_is_structural(tmp_path):
    code = main([
        "run",
        "--config", str(REPO / "config" / "allocation_config.json"),
        "--data", str(tmp_path),  # empty: no feed files
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2
