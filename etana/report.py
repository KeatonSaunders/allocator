"""Report writers: both deliverables from one run.

Markdown for humans, CSV for machines. kWh figures are *displayed* at 3 dp in
markdown while the CSVs carry full precision; ZAR is rounded exactly once —
here, at the end of the pipeline, never upstream.
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


def _md_table(frame: pd.DataFrame) -> str:
    """Minimal GitHub-markdown table (no tabulate dependency)."""
    cols = [str(c) for c in frame.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + " --- |" * len(cols)]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def _kwh(value: float) -> str:
    return "—" if pd.isna(value) else f"{value:,.3f}"


def findings_frame(findings: list[QualityFinding]) -> pd.DataFrame:
    """Findings as a frame, most severe first — the CSV deliverable."""
    frame = pd.DataFrame([asdict(f) for f in findings])
    return frame.sort_values(
        by="severity", key=lambda s: s.map(_SEVERITY_ORDER), kind="stable"
    ).reset_index(drop=True)


def _reconciliation(assessed: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per meter: interval accounting by quality flag, plus unbilled nulls."""
    flags = [f.value for f in QualityFlag if f is not QualityFlag.MISSING]
    rows = {}
    for meter, frame in assessed.items():
        counts = frame["flag"].value_counts()
        rows[meter] = {
            "intervals": len(frame),
            **{flag: int(counts.get(flag, 0)) for flag in flags},
            "unbilled_null": int(frame["kwh"].isna().sum()),
            "total_kwh": _kwh(frame["kwh"].sum()),
        }
    table = pd.DataFrame(rows).T.rename_axis("meter").reset_index()
    return table


def quality_report(
    cfg: Config,
    contracts: dict[str, FeedContract],
    findings: list[QualityFinding],
    assessed: dict[str, pd.DataFrame],
) -> str:
    month = f"{cfg.billing_year:04d}-{cfg.billing_month:02d}"
    frame = findings_frame(findings)

    def _range(row) -> str:
        if pd.isna(row["start"]):
            return "—"
        if pd.isna(row["end"]) or row["end"] == row["start"]:
            return str(row["start"])
        return f"{row['start']} → {row['end']}"

    frame["range"] = frame.apply(_range, axis=1)
    display = frame[["meter", "check", "severity", "range", "detail", "action"]]
    issues = display[display["severity"] != Severity.INFO]
    passed = display[display["severity"] == Severity.INFO]

    return "\n\n".join([
        f"# Data Quality Report — {month}",
        "Every quirk found is listed with the action taken; checks that ran and "
        "passed are listed too, because a verified invariant is evidence.",
        "## Feed contracts (as asserted in code)",
        _md_table(pd.DataFrame([asdict(c) for c in contracts.values()])),
        "## Interval reconciliation per meter",
        f"Expected intervals per meter: **{len(next(iter(assessed.values())))}** "
        "(full billing month, 30-min, SAST, interval-ending). `unbilled_null` "
        "counts intervals excluded from billing (refused gaps).",
        _md_table(_reconciliation(assessed)),
        "## Findings (action taken on every one)",
        _md_table(issues) if len(issues) else "None.",
        "## Checks that ran and passed",
        _md_table(passed) if len(passed) else "None recorded.",
    ]) + "\n"


def summary_report(
    cfg: Config,
    summary: pd.DataFrame,
    alloc: Allocation,
    billed_by_flag: pd.DataFrame,
) -> str:
    month = f"{cfg.billing_year:04d}-{cfg.billing_month:02d}"
    names = {site.meter: site.name for site in cfg.sites}

    per_bucket = summary.reset_index()
    per_bucket.insert(0, "site_name", per_bucket["site"].map(names))
    display = pd.DataFrame({
        "site": per_bucket["site_name"] + " (" + per_bucket["site"] + ")",
        "tou": per_bucket["tou"],
        "consumption_kwh": per_bucket["consumption_kwh"].map(_kwh),
        "allocated_kwh": per_bucket["allocated_kwh"].map(_kwh),
        "residual_kwh": per_bucket["residual_kwh"].map(_kwh),
        "rate_zar_per_kwh": per_bucket["rate_zar_per_kwh"],
        "wheeling_zar": per_bucket["wheeling_zar"].map(lambda v: f"{v:,.2f}"),
        "excluded_intervals": per_bucket["excluded_intervals"].astype(int),
    })

    # Site totals: ZAR rounded ONCE from the full-precision sum — the invoice
    # figure — so it may differ from the sum of displayed cells by a cent.
    totals = summary.groupby("site", sort=False).sum()
    site_totals = pd.DataFrame({
        "site": [f"{names[m]} ({m})" for m in totals.index],
        "consumption_kwh": totals["consumption_kwh"].map(_kwh).values,
        "allocated_kwh": totals["allocated_kwh"].map(_kwh).values,
        "residual_kwh": totals["residual_kwh"].map(_kwh).values,
        "wheeling_zar": totals["wheeling_zar"].map(lambda v: f"{v:,.2f}").values,
    })

    generation_total = float(alloc.generation.sum())
    allocated_total = float(alloc.allocated.sum().sum())
    unallocated_total = float(alloc.unallocated.sum())

    flag_display = billed_by_flag.copy()
    flag_display.insert(0, "site", [f"{names[m]} ({m})" for m in flag_display.index])
    for col in billed_by_flag.columns:
        flag_display[col] = billed_by_flag[col].map(_kwh)

    return "\n\n".join([
        f"# Monthly Summary — {month}",
        f"Generator: **{cfg.generator.name}** ({cfg.generator.meter}, "
        f"{cfg.generator.capacity_mw} MW). "
        "**Billing basis: wheeling is charged on ALLOCATED energy × TOU rate — "
        "not on consumption.** Consumption is shown for reference; residual "
        "(consumption − allocated) is grid purchase, not billed here.",
        "## Per site × TOU period",
        _md_table(display),
        "## Site totals (invoice figures)",
        _md_table(site_totals),
        f"Total wheeling billed: **ZAR {totals['wheeling_zar'].sum():,.2f}**.",
        "## Generation account",
        _md_table(pd.DataFrame([{
            "generation_kwh": _kwh(generation_total),
            "allocated_kwh": _kwh(allocated_total),
            "unallocated_kwh": _kwh(unallocated_total),
            "unallocated_pct": f"{100 * unallocated_total / generation_total:.1f}%",
        }])),
        "## Billed (allocated) energy by data quality flag",
        "Interpolated/estimated energy is billed but traceable; suspect energy "
        "is billed on meter readings that carry an open finding.",
        _md_table(flag_display),
        "*kWh cells are displayed at 3 dp (full precision in the CSV); each ZAR "
        "figure is rounded once from full precision, so displayed cells may "
        "differ from totals by a cent.*",
    ]) + "\n"


def write_all(
    out_dir: Path,
    cfg: Config,
    contracts: dict[str, FeedContract],
    findings: list[QualityFinding],
    assessed: dict[str, pd.DataFrame],
    alloc: Allocation,
    summary: pd.DataFrame,
    billed_by_flag: pd.DataFrame,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = summary.copy()
    summary_csv["wheeling_zar_rounded"] = summary_csv["wheeling_zar"].round(2)

    written = {
        out_dir / "quality_report.md": quality_report(cfg, contracts, findings, assessed),
        out_dir / "monthly_summary.md": summary_report(cfg, summary, alloc, billed_by_flag),
    }
    for path, text in written.items():
        path.write_text(text, encoding="utf-8")
    findings_frame(findings).to_csv(out_dir / "findings.csv", index=False)
    summary_csv.to_csv(out_dir / "monthly_summary.csv")

    paths = [*written, out_dir / "findings.csv", out_dir / "monthly_summary.csv"]
    log.info("reports_written", extra=kv(files=",".join(p.name for p in paths), out=str(out_dir)))
    return paths
