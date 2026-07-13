"""Typed loader for allocation_config.json.

Structural problems (missing file, missing key, unparseable value, incoherent
TOU windows) raise ConfigError; the CLI reports and exits non-zero. Nothing
about sites, percentages, TOU boundaries or rates exists anywhere else in the
code — this module is the single source.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(Exception):
    """The config file itself is unusable — fail loud before touching any data."""


@dataclass(frozen=True)
class Generator:
    name: str
    meter: str
    capacity_mw: float


@dataclass(frozen=True)
class Site:
    name: str
    meter: str
    provider: str
    allocation_pct: float

    @property
    def allocation_fraction(self) -> float:
        return self.allocation_pct / 100.0


@dataclass(frozen=True)
class Window:
    """Half-open [start, end) wall-clock window; applies on weekdays only."""

    start: time
    end: time


@dataclass(frozen=True)
class TouScheme:
    """Explicit weekday windows per bucket. Everything else — uncovered weekday
    hours and all weekend hours — falls to the complement bucket, so 24h/7d
    coverage holds by construction and overlap is the only possible incoherence.
    """

    weekday_windows: dict[str, tuple[Window, ...]]
    complement_bucket: str

    @property
    def buckets(self) -> frozenset[str]:
        return frozenset(self.weekday_windows) | {self.complement_bucket}


@dataclass(frozen=True)
class Config:
    billing_year: int
    billing_month: int
    timezone: ZoneInfo
    generator: Generator
    sites: tuple[Site, ...]
    tou: TouScheme
    rates_zar_per_kwh: dict[str, float]

    @property
    def allocation_pct_total(self) -> float:
        return sum(site.allocation_pct for site in self.sites)


def _require(mapping, key: str, where: str):
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"{where}: missing required key {key!r}")
    return mapping[key]


def _number(value, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: expected a number, got {value!r}") from None


def _parse_billing_month(text) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(text))
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ConfigError(f"billing_month: expected 'YYYY-MM', got {text!r}")
    return int(match.group(1)), int(match.group(2))


def _parse_timezone(text) -> ZoneInfo:
    # The config value is prose ("Africa/Johannesburg (SAST, UTC+2, no DST)");
    # the leading token is the IANA name.
    token = str(text).split()[0] if str(text).split() else ""
    try:
        return ZoneInfo(token)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"timezone: {text!r} does not start with an IANA zone name") from None


_HHMM_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def _parse_tou(periods) -> TouScheme:
    """I'm not a huge fan of this, but choosing it over hard-coding. I assume
    in practice this might come from an API or DB config so we wouldn't have
    to parse it like this.

    TOU windows arrive as prose ("Weekdays 07:00-10:00 and 18:00-20:00").
    Every HH:MM-HH:MM range in a bucket's text is one weekday window; a bucket
    with no ranges ("All other times...") is the complement. 'note' is
    documentation, not a bucket.
    """
    windows: dict[str, tuple[Window, ...]] = {}
    complements: list[str] = []
    for bucket, text in periods.items():
        if bucket == "note":
            continue
        found = _HHMM_RANGE.findall(str(text))
        if not found:
            complements.append(bucket)
            continue
        try:
            parsed = tuple(
                Window(time(int(h1), int(m1)), time(int(h2), int(m2)))
                for h1, m1, h2, m2 in found
            )
        except ValueError as exc:
            raise ConfigError(f"tou_periods_sast.{bucket}: {exc}") from None
        for window in parsed:
            if window.start >= window.end:
                raise ConfigError(
                    f"tou_periods_sast.{bucket}: window "
                    f"{window.start}-{window.end} is empty or inverted"
                )
        windows[bucket] = parsed
    if len(complements) != 1:
        raise ConfigError(
            "tou_periods_sast: expected exactly one catch-all bucket without "
            f"time ranges, found {complements!r}"
        )
    flat = sorted(
        (window.start, window.end, bucket)
        for bucket, ws in windows.items()
        for window in ws
    )
    for (s1, e1, b1), (s2, e2, b2) in zip(flat, flat[1:]):
        if s2 < e1:
            raise ConfigError(
                f"tou_periods_sast: {b1} {s1}-{e1} overlaps {b2} {s2}-{e2}"
            )
    return TouScheme(weekday_windows=windows, complement_bucket=complements[0])


def load_config(path: Path | str) -> Config:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: not valid JSON ({exc})") from None

    year, month = _parse_billing_month(_require(raw, "billing_month", "config"))
    tz = _parse_timezone(_require(raw, "timezone", "config"))

    gen_raw = _require(raw, "generator", "config")
    generator = Generator(
        name=str(_require(gen_raw, "name", "generator")),
        meter=str(_require(gen_raw, "meter", "generator")),
        capacity_mw=_number(_require(gen_raw, "capacity_mw", "generator"), "generator.capacity_mw"),
    )
    if generator.capacity_mw <= 0:
        raise ConfigError(f"generator.capacity_mw: must be positive, got {generator.capacity_mw}")

    sites_raw = _require(raw, "sites", "config")
    if not isinstance(sites_raw, list) or not sites_raw:
        raise ConfigError("config: 'sites' must be a non-empty list")
    sites = []
    for i, site_raw in enumerate(sites_raw):
        where = f"sites[{i}]"
        pct = _number(_require(site_raw, "allocation_pct", where), f"{where}.allocation_pct")
        if not 0 <= pct <= 100:
            raise ConfigError(f"{where}.allocation_pct: {pct} outside 0..100")
        sites.append(
            Site(
                name=str(_require(site_raw, "site", where)),
                meter=str(_require(site_raw, "meter", where)),
                provider=str(_require(site_raw, "provider", where)),
                allocation_pct=pct,
            )
        )
    meters = [site.meter for site in sites]
    if len(set(meters)) != len(meters):
        raise ConfigError(f"sites: meter ids must be unique, got {meters}")

    tou = _parse_tou(_require(raw, "tou_periods_sast", "config"))
    rates_raw = _require(raw, "wheeling_rates_zar_per_kwh", "config")
    rates = {
        bucket: _number(value, f"wheeling_rates_zar_per_kwh.{bucket}")
        for bucket, value in rates_raw.items()
    }
    if set(rates) != tou.buckets:
        raise ConfigError(
            f"every TOU bucket needs a rate and vice versa: "
            f"rates={sorted(rates)} buckets={sorted(tou.buckets)}"
        )

    return Config(
        billing_year=year,
        billing_month=month,
        timezone=tz,
        generator=generator,
        sites=tuple(sites),
        tou=tou,
        rates_zar_per_kwh=rates,
    )
