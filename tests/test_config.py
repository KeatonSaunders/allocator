"""Stage 2: the config loader accepts the real config and rejects malformed ones
with a clear message and, via the CLI, a non-zero exit."""

import json
from datetime import time
from pathlib import Path

import pytest

from etana.cli import main
from etana.config import ConfigError, load_config

REPO = Path(__file__).resolve().parents[1]
REAL_CONFIG = REPO / "config" / "allocation_config.json"


def valid() -> dict:
    """A known-good config dict to mutate per test — the real file itself."""
    return json.loads(REAL_CONFIG.read_text(encoding="utf-8"))


def write(tmp_path: Path, cfg: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_real_config_loads_typed():
    cfg = load_config(REAL_CONFIG)
    assert (cfg.billing_year, cfg.billing_month) == (2026, 6)
    assert cfg.timezone.key == "Africa/Johannesburg"
    assert cfg.generator.meter == "GEN-RH-01"
    assert [s.meter for s in cfg.sites] == ["MTR-1001", "MTR-1002", "PT-77"]
    assert cfg.sites[0].allocation_fraction == pytest.approx(0.40)
    assert cfg.allocation_pct_total == pytest.approx(100.0)
    # TOU prose parsed into windows: peak has two, offpeak is the complement.
    peak = cfg.tou.weekday_windows["peak"]
    assert [(w.start, w.end) for w in peak] == [(time(7), time(10)), (time(18), time(20))]
    assert len(cfg.tou.weekday_windows["standard"]) == 3
    assert cfg.tou.complement_bucket == "offpeak"
    assert cfg.rates_zar_per_kwh == {"peak": 2.5, "standard": 1.8, "offpeak": 1.1}


def test_missing_file_fails_with_path_in_message(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.json")


def test_missing_key_fails_loud(tmp_path):
    cfg = valid()
    del cfg["sites"]
    with pytest.raises(ConfigError, match="sites"):
        load_config(write(tmp_path, cfg))


def test_unparseable_pct_fails_loud(tmp_path):
    cfg = valid()
    cfg["sites"][0]["allocation_pct"] = "forty"
    with pytest.raises(ConfigError, match="allocation_pct"):
        load_config(write(tmp_path, cfg))


def test_overlapping_tou_windows_rejected(tmp_path):
    cfg = valid()
    cfg["tou_periods_sast"]["standard"] = "Weekdays 09:00-11:00 and 20:00-22:00"
    with pytest.raises(ConfigError, match="overlaps"):
        load_config(write(tmp_path, cfg))


def test_two_catch_all_buckets_rejected(tmp_path):
    cfg = valid()
    cfg["tou_periods_sast"]["night"] = "All remaining hours"
    with pytest.raises(ConfigError, match="catch-all"):
        load_config(write(tmp_path, cfg))


def test_missing_rate_for_bucket_rejected(tmp_path):
    cfg = valid()
    del cfg["wheeling_rates_zar_per_kwh"]["offpeak"]
    with pytest.raises(ConfigError, match="rate"):
        load_config(write(tmp_path, cfg))


def test_cli_nonzero_exit_and_message_on_malformed(tmp_path, capsys):
    cfg = valid()
    del cfg["wheeling_rates_zar_per_kwh"]
    path = write(tmp_path, cfg)
    assert main(["run", "--config", str(path)]) != 0
    err = capsys.readouterr().err
    assert "config_invalid" in err and "wheeling_rates_zar_per_kwh" in err


def test_cli_zero_exit_on_real_config(tmp_path, capsys):
    args = ["--data", str(REPO / "data"), "--out", str(tmp_path / "out")]
    assert main(["run", "--config", str(REAL_CONFIG), *args]) == 0
    assert "run_start" in capsys.readouterr().err


def test_pct_not_summing_to_100_is_finding_not_crash(tmp_path, capsys):
    cfg = valid()
    cfg["sites"][0]["allocation_pct"] = 10.0  # total now 70
    path = write(tmp_path, cfg)
    assert load_config(path).allocation_pct_total == pytest.approx(70.0)
    args = ["--data", str(REPO / "data"), "--out", str(tmp_path / "out")]
    assert main(["run", "--config", str(path), *args]) == 0  # continue, don't crash
    assert "allocation_pct_total_not_100" in capsys.readouterr().err
