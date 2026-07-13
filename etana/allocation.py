"""Allocation core: pure functions over the canonical frames, no I/O.

Per half-hour, per site:

    entitlement = allocation_fraction x generation   # share of this half-hour's output
    allocated   = min(entitlement, consumption)      # the cap — see below
    residual    = consumption - allocated            # bought from the grid
    unallocated = generation - sum(allocated)        # output with nowhere to go

The min() is the heart of the model: energy delivered to a site cannot exceed
the energy the site consumed in that same half-hour, because there is no
storage in this model — entitlement unmatched by simultaneous consumption is
simply not wheeled.

Everything is matrix arithmetic on interval x site frames (vectorised, one
expression per quantity), and everything stays at FULL precision: rounding
happens once, in the reporting layer, on final ZAR amounts only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Absolute kWh slack for float-identity assertions only — never a rounding
# of values. Six decimal places below the milliwatt-hour.
_EPS = 1e-9


@dataclass(frozen=True)
class Allocation:
    """Per-interval allocation, interval x site (generation/unallocated are
    per-interval series). Immutable; aggregation reads it, never edits it."""

    generation: pd.Series
    consumption: pd.DataFrame
    entitlement: pd.DataFrame
    allocated: pd.DataFrame
    residual: pd.DataFrame
    unallocated: pd.Series


def allocate(
    generation: pd.Series,
    consumption: pd.DataFrame,
    fractions: dict[str, float],
) -> Allocation:
    """Allocate each half-hour of generation across sites.

    `consumption`: one column per site meter, on the canonical grid.
    A NaN value (a refused gap) stays NaN: energy we cannot measure is neither
    allocated nor billed, and the exclusion surfaces in the summary.
    """
    fraction = pd.Series(fractions, dtype="float64")
    if set(fraction.index) != set(consumption.columns):
        raise ValueError(
            f"fractions and consumption disagree on sites: "
            f"{sorted(fraction.index)} vs {sorted(consumption.columns)}"
        )
    consumption = consumption[list(fraction.index)]  # one column order throughout

    entitlement = pd.DataFrame(
        np.outer(generation, fraction),
        index=generation.index,
        columns=fraction.index,
    )
    allocated = np.minimum(entitlement, consumption)
    residual = consumption - allocated
    # Energy not known to be delivered is not counted as delivered: NaN
    # allocations contribute nothing to what the generator has given out.
    unallocated = generation - allocated.fillna(0).sum(axis=1)

    result = Allocation(generation, consumption, entitlement, allocated, residual, unallocated)
    _assert_invariants(result)
    return result


def _assert_invariants(a: Allocation) -> None:
    """The reconciliation identities, asserted on every execution.

    NaN rows are exempt by construction (NaN comparisons are False; allclose
    uses equal_nan) — they represent excluded, not zero, energy. A negative
    unallocated can only mean over-allocation (configured fractions > 100%
    with the cap not binding): there is no defensible invoice, so crash.
    """
    assert not (a.allocated > a.consumption + _EPS).any().any(), "allocated exceeds consumption"
    assert not (a.allocated > a.entitlement + _EPS).any().any(), "allocated exceeds entitlement"
    assert not (a.allocated < -_EPS).any().any(), "negative allocation"
    assert not (a.residual < -_EPS).any().any(), "negative residual"
    assert not (a.unallocated < -_EPS).any(), "negative unallocated: over-allocated generation"
    assert np.allclose(
        a.consumption, a.allocated + a.residual, atol=_EPS, equal_nan=True
    ), "consumption != allocated + residual"
    assert np.allclose(
        a.generation, a.allocated.fillna(0).sum(axis=1) + a.unallocated, atol=_EPS, equal_nan=True
    ), "generation != sum(allocated) + unallocated"


def summarise(a: Allocation, buckets: pd.Series, rates: dict[str, float]) -> pd.DataFrame:
    """Aggregate to (site x TOU bucket) at FULL precision.

    Wheeling is billed on ALLOCATED energy x TOU rate — not consumption; the
    summary carries both so the report can state which one the money follows.
    `excluded_intervals` makes refused-gap exclusions visible next to the
    totals they are absent from.
    """
    parts = {
        "consumption_kwh": a.consumption.groupby(buckets).sum(),
        "allocated_kwh": a.allocated.groupby(buckets).sum(),
        "residual_kwh": a.residual.groupby(buckets).sum(),
        "excluded_intervals": a.consumption.isna().groupby(buckets).sum(),
    }
    table = pd.DataFrame({name: frame.stack() for name, frame in parts.items()})
    table.index.names = ["tou", "site"]
    table = table.swaplevel().sort_index()
    # Deterministic, config-ordered rows: sites as configured, buckets as rated.
    table = table.reindex(
        pd.MultiIndex.from_product(
            [a.consumption.columns, list(rates)], names=["site", "tou"]
        )
    )
    rate = table.index.get_level_values("tou").map(rates)
    table["rate_zar_per_kwh"] = rate
    table["wheeling_zar"] = table["allocated_kwh"] * rate
    return table
