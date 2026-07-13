"""Stage 9: the cap below/above/equal to consumption, the per-interval
invariants, zero generation, zero consumption, NaN exclusion — and on the real
month, the sanity shape: cap binds, residual near zero, unallocated large."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etana.adapters import generation, meterflow, powertrack
from etana.allocation import allocate, summarise
from etana.canonical import month_grid
from etana.config import load_config
from etana.quality import assess
from etana.tou import classify

REPO = Path(__file__).resolve().parents[1]
CFG = load_config(REPO / "config" / "allocation_config.json")
GRID = month_grid(2026, 6, CFG.timezone)


def one_interval(gen: float, cons: dict[str, float], fractions: dict[str, float]):
    index = GRID[:1]
    return allocate(
        pd.Series([gen], index=index),
        pd.DataFrame({m: [v] for m, v in cons.items()}, index=index),
        fractions,
    )


def test_cap_entitlement_below_above_and_equal_to_consumption():
    # Fractions must sum <= 1 or the unallocated >= 0 invariant (rightly) fires.
    a = one_interval(
        1000.0,
        {"BELOW": 500.0, "ABOVE": 300.0, "EQUAL": 250.0},
        {"BELOW": 0.40, "ABOVE": 0.35, "EQUAL": 0.25},  # entitlements 400/350/250
    )
    row = a.allocated.iloc[0]
    assert row["BELOW"] == 400.0  # entitlement < consumption: entitlement wins
    assert row["ABOVE"] == 300.0  # entitlement > consumption: cap binds
    assert row["EQUAL"] == 250.0  # equal: either way, same number
    assert a.residual.iloc[0].to_dict() == {"BELOW": 100.0, "ABOVE": 0.0, "EQUAL": 0.0}
    assert a.unallocated.iloc[0] == 1000.0 - 400.0 - 300.0 - 250.0


def test_over_allocation_invariant_refuses_to_produce_an_invoice():
    # 120% configured with the cap not binding -> negative unallocated is a
    # broken model, not a billing outcome.
    with pytest.raises(AssertionError, match="over-allocated"):
        one_interval(1000.0, {"S1": 900.0, "S2": 900.0}, {"S1": 0.6, "S2": 0.6})


def test_zero_generation_means_all_grid():
    a = one_interval(0.0, {"S1": 250.0}, {"S1": 0.4})
    assert a.allocated.iloc[0, 0] == 0.0
    assert a.residual.iloc[0, 0] == 250.0  # everything bought from the grid
    assert a.unallocated.iloc[0] == 0.0


def test_zero_consumption_means_nothing_wheeled():
    a = one_interval(1000.0, {"S1": 0.0}, {"S1": 0.4})
    assert a.allocated.iloc[0, 0] == 0.0  # no simultaneous consumption, no delivery
    assert a.residual.iloc[0, 0] == 0.0
    assert a.unallocated.iloc[0] == 1000.0


def test_nan_consumption_is_excluded_not_zeroed():
    a = one_interval(1000.0, {"S1": np.nan, "S2": 100.0}, {"S1": 0.4, "S2": 0.4})
    assert pd.isna(a.allocated.iloc[0]["S1"])  # unknown, not zero
    assert a.allocated.iloc[0]["S2"] == 100.0
    # The NaN site contributes nothing to the pool's usage.
    assert a.unallocated.iloc[0] == 900.0


def test_mismatched_sites_fail_loud():
    with pytest.raises(ValueError, match="disagree on sites"):
        one_interval(1.0, {"S1": 1.0}, {"OTHER": 0.4})


def test_summarise_bills_on_allocated_at_full_precision():
    index = GRID[:2]  # Monday 00:30, 01:00 -> both offpeak
    gen = pd.Series([1000.0, 1000.0], index=index)
    cons = pd.DataFrame({"S1": [333.333333, np.nan]}, index=index)
    table = summarise(
        allocate(gen, cons, {"S1": 0.25}), classify(index, CFG.tou), CFG.rates_zar_per_kwh
    )
    row = table.loc[("S1", "offpeak")]
    assert row["allocated_kwh"] == 250.0  # entitlement, cap not binding
    assert row["consumption_kwh"] == pytest.approx(333.333333)
    assert row["wheeling_zar"] == pytest.approx(250.0 * 1.1)  # allocated x rate, unrounded
    assert row["excluded_intervals"] == 1  # the NaN interval, visible
    assert set(table.index.get_level_values("tou")) == set(CFG.rates_zar_per_kwh)


def test_real_month_has_the_expected_shape():
    findings: list = []
    feeds = (
        meterflow.load(REPO / "data" / "meterflow_202606.csv", GRID, findings)
        | powertrack.load(REPO / "data" / "powertrack_202606.csv", GRID, findings)
        | generation.load(
            REPO / "data" / "riverside_hydro_generation_202606.csv", GRID, CFG.generator, findings
        )
    )
    assessed = {meter: assess(frame, findings) for meter, frame in feeds.items()}
    gen = assessed[CFG.generator.meter]["kwh"]
    cons = pd.DataFrame({site.meter: assessed[site.meter]["kwh"] for site in CFG.sites})
    a = allocate(gen, cons, {site.meter: site.allocation_fraction for site in CFG.sites})
    table = summarise(a, classify(GRID, CFG.tou), CFG.rates_zar_per_kwh)

    # Generation substantially exceeds consumption, so the cap binds nearly
    # everywhere: residual near zero, unallocated large. If this shape breaks,
    # something upstream is wrong.
    # Measured on this month: binds 81-94% per site, residual 3.6% of
    # consumption, unallocated 38.5% of generation. Margins are set to catch a
    # broken pipeline (a unit or convention error flips them completely), not
    # to pin exact values.
    cap_binds = (a.allocated == a.consumption).mean()
    assert cap_binds.min() > 0.75 and cap_binds.mean() > 0.85
    assert a.residual.sum().sum() < 0.05 * a.consumption.sum().sum()
    assert a.unallocated.sum() > 0.3 * gen.sum()
    # Nothing was dropped: no excluded intervals this month (all gaps filled).
    assert table["excluded_intervals"].sum() == 0
    # And the money follows allocated energy exactly, at full precision.
    assert (
        table["wheeling_zar"] == table["allocated_kwh"] * table["rate_zar_per_kwh"]
    ).all()
