"""Report writers: both deliverables from one run, one source of truth.

Every table is built exactly once, as a full-precision DataFrame. Each frame
is written verbatim as its own CSV (the machine layer) and rendered into a
markdown report (the human layer), so the markdown can never carry data the
CSVs lack. Display formatting — kWh at 3 dp, ZAR at 2 dp — lives only in the
renderer; actual ZAR *rounding* happens exactly once, in the `invoice_zar`
column of site_totals, never upstream.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .adapters.base import FeedContract
from .allocation import Allocation
from .canonical import QualityFlag
from .config import Config
from .log import kv
from .quality import QualityFinding, Severity

log = logging.getLogger("etana.report")

_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}

# Every flag a billed interval can carry (MISSING cannot survive assess()).
_FLAGS = [f.value for f in QualityFlag if f is not QualityFlag.MISSING]


def _fmt(col: str, value) -> str:
    """Column-name-driven display formatting — markdown only, CSVs stay raw."""
    if pd.isna(value):
        return "—"
    if col.endswith("_zar_per_kwh"):
        return str(value)  # the configured rate, verbatim
    if col.endswith("_kwh"):
        return f"{value:,.3f}"
    if col.endswith("_zar"):
        return f"{value:,.2f}"
    if col.endswith("_pct"):
        return f"{value:.1f}%"
    return str(value)


def _md_table(frame: pd.DataFrame) -> str:
    """Minimal GitHub-markdown rendering of one section frame."""
    cols = [str(c) for c in frame.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    for row in frame.itertuples(index=False):
        lines.append("| " + " | ".join(_fmt(c, v) for c, v in zip(cols, row)) + " |")
    return "\n".join(lines)


def findings_frame(findings: list[QualityFinding]) -> pd.DataFrame:
    """Findings as a frame, most severe first."""
    frame = pd.DataFrame([asdict(f) for f in findings])
    return frame.sort_values(
        by="severity", key=lambda s: s.map(_SEVERITY_ORDER), kind="stable"
    ).reset_index(drop=True)


def _reconciliation(assessed: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per meter: interval accounting by quality flag, plus unbilled nulls."""
    rows = [
        {
            "meter": meter,
            "intervals": len(frame),
            **{flag: int(frame["flag"].eq(flag).sum()) for flag in _FLAGS},
            "unbilled_null": int(frame["kwh"].isna().sum()),
            "total_kwh": frame["kwh"].sum(),
        }
        for meter, frame in assessed.items()
    ]
    return pd.DataFrame(rows)


def quality_frames(
    contracts: dict[str, FeedContract],
    findings: list[QualityFinding],
    assessed: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """The quality report's tables, one full-precision frame each."""
    return {
        "feed_contracts": pd.DataFrame([asdict(c) for c in contracts.values()]),
        "interval_reconciliation": _reconciliation(assessed),
        "findings": findings_frame(findings),
    }


def summary_frames(
    cfg: Config,
    alloc: Allocation,
    summary: pd.DataFrame,
    assessed: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """The monthly summary's tables, one full-precision frame each."""
    names = {site.meter: site.name for site in cfg.sites}

    per_bucket = summary.rename_axis(["meter", "tou"]).reset_index()
    per_bucket.insert(1, "site", per_bucket["meter"].map(names))
    per_bucket["excluded_intervals"] = per_bucket["excluded_intervals"].astype(int)

    energy = ["consumption_kwh", "allocated_kwh", "residual_kwh", "wheeling_zar"]
    totals = summary.groupby("site", sort=False)[energy].sum().rename_axis("meter").reset_index()
    totals.insert(1, "site", totals["meter"].map(names))
    # THE single rounding point of the pipeline (D24): each site's invoice
    # figure, rounded once from the full-precision sum. Nothing is billed at
    # bucket grain, so per-bucket ZAR is never rounded at all.
    totals["invoice_zar"] = totals["wheeling_zar"].round(2)

    generation_total = float(alloc.generation.sum())
    unallocated_total = float(alloc.unallocated.sum())
    account = pd.DataFrame([{
        "generation_kwh": generation_total,
        "allocated_kwh": float(alloc.allocated.sum().sum()),
        "unallocated_kwh": unallocated_total,
        "unallocated_pct": 100 * unallocated_total / generation_total,
    }])

    # Billed (allocated) energy split by each site's quality flags, so an
    # invoice can be defended row by row.
    billed = pd.DataFrame([
        {
            "meter": site.meter,
            "site": site.name,
            **{
                f"{flag}_kwh": alloc.allocated[site.meter][
                    assessed[site.meter]["flag"].eq(flag)
                ].sum()
                for flag in _FLAGS
            },
        }
        for site in cfg.sites
    ])

    return {
        "monthly_summary": per_bucket,
        "site_totals": totals,
        "generation_account": account,
        "billed_by_flag": billed,
    }


def quality_markdown(cfg: Config, frames: dict[str, pd.DataFrame]) -> str:
    month = f"{cfg.billing_year:04d}-{cfg.billing_month:02d}"
    findings = frames["findings"]
    issues = findings[findings["severity"] != Severity.INFO]
    passed = findings[findings["severity"] == Severity.INFO]

    return "\n\n".join([
        f"# Data Quality Report — {month}",
        "Every quirk found is listed with the action taken; checks that ran and "
        "passed are listed too, because a verified invariant is evidence.",
        "## Feed contracts (as asserted in code)",
        _md_table(frames["feed_contracts"]),
        "## Interval reconciliation per meter",
        f"Expected intervals per meter: "
        f"**{frames['interval_reconciliation']['intervals'].iat[0]}** "
        "(full billing month, 30-min, SAST, interval-ending). `unbilled_null` "
        "counts intervals excluded from billing (refused gaps).",
        _md_table(frames["interval_reconciliation"]),
        "## Findings (action taken on every one)",
        _md_table(issues) if len(issues) else "None.",
        "## Checks that ran and passed",
        _md_table(passed) if len(passed) else "None recorded.",
    ]) + "\n"


def summary_markdown(cfg: Config, frames: dict[str, pd.DataFrame]) -> str:
    month = f"{cfg.billing_year:04d}-{cfg.billing_month:02d}"
    totals = frames["site_totals"]

    return "\n\n".join([
        f"# Monthly Summary — {month}",
        f"Generator: **{cfg.generator.name}** ({cfg.generator.meter}, "
        f"{cfg.generator.capacity_mw} MW). "
        "**Billing basis: wheeling is charged on ALLOCATED energy × TOU rate — "
        "not on consumption.** Consumption is shown for reference; residual "
        "(consumption − allocated) is grid purchase, not billed here.",
        "## Per site × TOU period",
        _md_table(frames["monthly_summary"]),
        "## Site totals",
        "`invoice_zar` is the pipeline's single ZAR rounding point: rounded "
        "once from the site's full-precision sum, so it may differ from "
        "summing displayed per-bucket cells by a cent.",
        _md_table(totals),
        f"Total wheeling billed: **ZAR {totals['invoice_zar'].sum():,.2f}**.",
        "## Generation account",
        _md_table(frames["generation_account"]),
        "## Billed (allocated) energy by data quality flag",
        "Interpolated/estimated energy is billed but traceable; suspect energy "
        "is billed on meter readings that carry an open finding.",
        _md_table(frames["billed_by_flag"]),
        "*kWh cells are displayed at 3 dp; every table above is also written "
        "as a CSV at full precision.*",
    ]) + "\n"


def write_all(
    out_dir: Path,
    cfg: Config,
    contracts: dict[str, FeedContract],
    findings: list[QualityFinding],
    assessed: dict[str, pd.DataFrame],
    alloc: Allocation,
    summary: pd.DataFrame,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    quality = quality_frames(contracts, findings, assessed)
    money = summary_frames(cfg, alloc, summary, assessed)

    documents = {
        "quality_report.md": quality_markdown(cfg, quality),
        "monthly_summary.md": summary_markdown(cfg, money),
    }
    written = []
    for name, text in documents.items():
        (out_dir / name).write_text(text, encoding="utf-8")
        written.append(out_dir / name)
    for slug, frame in (quality | money).items():
        frame.to_csv(out_dir / f"{slug}.csv", index=False)
        written.append(out_dir / f"{slug}.csv")

    log.info("reports_written", extra=kv(files=",".join(p.name for p in written), out=str(out_dir)))
    return written
