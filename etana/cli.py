"""One command, both deliverables.

`python -m etana run --config ... --data ... --out ...` ingests the three
provider feeds onto the canonical grid, runs the quality engine, allocates,
and writes the data quality report and the monthly summary. Structural
problems (bad config, missing/ambiguous feed files, unknown meters) exit
non-zero; data problems become findings in the report and the run continues.
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from . import report
from .adapters import generation, meterflow, powertrack
from .adapters.base import FeedError
from .allocation import allocate, summarise
from .canonical import month_grid
from .config import Config, ConfigError, load_config
from .log import kv, setup
from .quality import QualityFinding, Severity, assess, note
from .tou import classify

log = logging.getLogger("etana.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etana",
        description="Energy wheeling allocation: data quality report + monthly summary",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="produce both deliverables for the billing month")
    run.add_argument("--config", type=Path, required=True, help="path to allocation_config.json")
    run.add_argument("--data", type=Path, default=Path("data"), help="directory with the raw meter files")
    run.add_argument("--out", type=Path, default=Path("out"), help="directory for the reports")
    return parser


def _find_feed_file(data_dir: Path, pattern: str, provider: str) -> Path:
    matches = sorted(data_dir.glob(pattern))
    if len(matches) != 1:
        raise FeedError(
            f"{provider}: expected exactly one file matching {pattern!r} in "
            f"{data_dir}, found {len(matches)}"
        )
    return matches[0]


def _run(cfg: Config, data_dir: Path, out_dir: Path) -> list[Path]:
    grid = month_grid(cfg.billing_year, cfg.billing_month, cfg.timezone)
    findings: list[QualityFinding] = []

    # Config-level finding (D7): percentages are applied exactly as configured.
    total_pct = cfg.allocation_pct_total
    note(
        findings, "config", "allocation_pct_total",
        Severity.INFO if abs(total_pct - 100.0) < 1e-9 else Severity.WARNING,
        f"configured allocation percentages sum to {total_pct}",
        "applied as configured",
    )
    if abs(total_pct - 100.0) >= 1e-9:
        log.warning("allocation_pct_total_not_100", extra=kv(total=total_pct))

    feeds: dict[str, pd.DataFrame] = {}
    for adapter in (meterflow, powertrack):
        path = _find_feed_file(data_dir, adapter.FILE_GLOB, adapter.CONTRACT.provider)
        feeds |= adapter.load(path, grid, findings)
    gen_path = _find_feed_file(data_dir, generation.FILE_GLOB, generation.CONTRACT.provider)
    feeds |= generation.load(gen_path, grid, cfg.generator, findings)

    # Reconcile the feeds against config: a configured meter without data is
    # structural (cannot bill a site on nothing); an unconfigured meter in the
    # data is a finding, not a crash.
    configured = {site.meter for site in cfg.sites} | {cfg.generator.meter}
    if missing := sorted(configured - set(feeds)):
        raise FeedError(f"no data found for configured meter(s): {missing}")
    for extra in sorted(set(feeds) - configured):
        note(findings, extra, "unconfigured_meter", Severity.WARNING,
             "meter present in the data but not in config", "ignored for billing")

    assessed = {meter: assess(feeds[meter], findings) for meter in sorted(configured)}

    gen_kwh = assessed[cfg.generator.meter]["kwh"]
    consumption = pd.DataFrame({s.meter: assessed[s.meter]["kwh"] for s in cfg.sites})
    alloc = allocate(gen_kwh, consumption, {s.meter: s.allocation_fraction for s in cfg.sites})
    summary = summarise(alloc, classify(grid, cfg.tou), cfg.rates_zar_per_kwh)

    contracts = {
        adapter.CONTRACT.provider: adapter.CONTRACT
        for adapter in (meterflow, powertrack, generation)
    }
    return report.write_all(out_dir, cfg, contracts, findings, assessed, alloc, summary)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup()
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("config_invalid", extra=kv(config=str(args.config), error=str(exc)))
        return 2

    log.info(
        "run_start",
        extra=kv(
            billing_month=f"{cfg.billing_year:04d}-{cfg.billing_month:02d}",
            timezone=cfg.timezone.key,
            generator=cfg.generator.meter,
            sites=",".join(site.meter for site in cfg.sites),
            allocation_pct_total=cfg.allocation_pct_total,
            data=str(args.data),
            out=str(args.out),
        ),
    )
    try:
        written = _run(cfg, args.data, args.out)
    except FeedError as exc:
        log.error("feed_invalid", extra=kv(error=str(exc)))
        return 2
    log.info("run_complete", extra=kv(files=",".join(p.name for p in written)))
    return 0


def entry() -> None:
    raise SystemExit(main())
