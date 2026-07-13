"""One command produces both deliverables.

Stage 2: load and validate config, log a structured start line, exit 0.
The pipeline (adapters -> quality -> TOU -> allocation -> reports) lands in
stages 4-10.
"""

import argparse
import logging
from pathlib import Path

from .config import ConfigError, load_config
from .log import kv, setup

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
    # Percentages not summing to 100 is a data-quality finding, not a crash:
    # they are applied exactly as configured and the report will say so.
    if abs(cfg.allocation_pct_total - 100.0) > 1e-9:
        log.warning("allocation_pct_total_not_100", extra=kv(total=cfg.allocation_pct_total))
    return 0


def entry() -> None:
    raise SystemExit(main())
