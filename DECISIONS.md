# Decisions register (working scratchpad — folded into README at Stage 10)

## Stage 1 — Feed contracts and observed file characteristics

Recon via `scratch/inspect.py` (throwaway, gitignored), stdlib only, run against the raw files
before any transform was written.

### Canonical target (restated for reference)

30-minute intervals, kWh, SAST (`Africa/Johannesburg`, UTC+2, no DST), **interval-ending**.
June 2026 = 1440 intervals:

- **Interval #1 ends `2026-06-01 00:30:00+02:00`** (covers 00:00–00:30 SAST on 1 June).
- **Interval #1440 ends `2026-07-01 00:00:00+02:00`** (covers 23:30–24:00 SAST on 30 June).

The interval *ending* 2026-06-01 00:00 SAST belongs to May and is excluded.

### FeedContract table (to be encoded in `adapters/base.py` and asserted at load)

| Field | meterflow (MTR-1001, MTR-1002) | powertrack (PT-77) | generation (GEN-RH-01) |
|---|---|---|---|
| Timezone | UTC (ISO-8601 `Z` suffix) | SAST (naive local) | SAST (naive local) |
| Interval convention | **ending** — confirmed by span | **beginning** — confirmed by span | documented ending; **data says beginning** (see D1) |
| Interval length | 30 min | 15 min | 30 min |
| Unit | kWh (energy) | **avg kW** → kWh = kW × 0.25 h | kWh (energy) |
| Sign | consumption positive; negatives impossible | consumption positive; negatives impossible | generation positive; negatives impossible |
| Register type | interval values | interval values | interval values |
| Delimiter | `,` | `;` | `,` |
| Decimal separator | `.` | `.` | `.` |
| Date format | ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`, single column | **`DD/MM/YYYY`** + separate `HH:MM` column | `YYYY-MM-DD HH:MM`, single column |
| Line endings | CRLF, no BOM, no trailing newline | CRLF, no BOM, no trailing newline | CRLF, no BOM, no trailing newline |
| Layout | long/stacked, 2 meters in one file | single meter | single meter, no meter-id column |
| First raw timestamp (verbatim) | `2026-05-31T22:30:00Z` | `01/06/2026` `00:00` | `2026-06-01 00:00` |
| Last raw timestamp (verbatim) | `2026-06-30T22:00:00Z` | `30/06/2026` `23:45` | `2026-06-30 23:30` |
| Expected rows | 1440 per meter (2880) | 2880 | 1440 |
| Actual data rows | 2873 (MTR-1001: 1428, MTR-1002: 1445) | 2878 | 1440 |

Convention cross-checks against the actual span:

- **meterflow**: `2026-05-31T22:30Z … 2026-06-30T22:00Z` is exactly SAST June under
  interval-*ending* (00:30 → 24:00 SAST) — documented convention **confirmed**.
- **powertrack**: `01/06 00:00 … 30/06 23:45` SAST is exactly June under interval-*beginning* —
  documented convention **confirmed**. `DD/MM` order proven by 1726 rows with day-field > 12.
- **generation**: see D1 below — documented convention **contradicted** by the data.

### Observed quirks (all to be surfaced in the data quality report, none pre-fixed here)

| # | Feed | Quirk | Detail |
|---|---|---|---|
| Q1 | MTR-1001 | 12-interval gap (6 h, contiguous) | Missing ending-labels `2026-06-12 06:30 … 12:00` SAST (`04:30…10:00Z`). Friday; crosses standard→peak→standard TOU boundaries. Sits exactly at the ≤12 profile-estimate threshold. |
| Q2 | MTR-1002 | 4 exact duplicates | `2026-06-04T22:30Z, 23:00Z, 23:30Z` and `2026-06-05T00:00Z`, identical values — safe to collapse, still reported. |
| Q3 | MTR-1002 | 1 conflicting duplicate | `2026-06-08T08:30Z`: `633.423` vs `1140.161`. Tie-break: last-received (file order) wins → `1140.161`; both values logged. |
| Q4 | PT-77 | 2 missing 15-min rows | Beginning-labels `2026-06-17 13:30` and `13:45` SAST — **both halves** of the canonical 30-min interval ending `14:00`, so one whole canonical interval is missing (not a partial pair). |
| Q5 | PT-77 | Frozen reading, 20 intervals (5 h) | Exactly `352.000` kW repeated, beginning-labels `2026-06-25 08:00 … 12:45` SAST (Thursday). Also the file minimum — suspicious round value. Flag `suspect`; do not assume cause. |
| Q6 | GEN | 24-interval run of exact zeros (12 h) | Labels `2026-06-15 06:00 … 17:30` (Monday). Outage, meter fault, or missing-coded-as-zero — flagged either way, not assumed. |
| Q7 | GEN | Interval convention contradicts docs | See D1. |

### Checks that ran and passed (evidence, not assumption)

- No BOM, no non-numeric or blank value cells, no leading/trailing whitespace, in any file.
- No negatives anywhere; no values above generator nameplate (max 2382.348 kWh < 2500 kWh
  = 5 MW × 0.5 h); consumption maxima plausible (MTR-1001 ≤ 941.9 kWh, MTR-1002 ≤ 1140.2 kWh
  — the conflict row, PT-77 ≤ 1045.8 kW).
- No duplicate keys in MTR-1001, PT-77, or GEN; no rows outside the June window in any feed.
- GEN and MTR-1002 interval counts reconcile exactly (after dedupe for MTR-1002).
- No frozen runs in MTR-1001 / MTR-1002.

### Decisions

**D1 — Generation labels are interval-beginning, overriding the documentation.**
CLAUDE.md documents the generation feed as interval-ending, but the file holds exactly 1440 rows
labelled `2026-06-01 00:00 … 2026-06-30 23:30` SAST. Under interval-ending, the first label would
be an interval belonging to May and June's final interval (ending `2026-07-01 00:00`) would be
absent — i.e. the file would cover the wrong month by one interval. Under interval-beginning it
covers June exactly. The data wins: **treat labels as interval-beginning; convert with +30 min**.
Recorded as a finding in the data quality report, since it contradicts the feed's documentation.

**D2 — MTR-1002 conflicting duplicate: last-received wins.** Providers re-send corrections, so
file order is treated as arrival order and the later row (`1140.161`) is kept. Every conflict is
logged individually with both values. (Exact duplicates are collapsed and counted separately —
they need no tie-break.) In future, it would be safer to check whether one of the readings is
in fact implausible for that day of week, time of day etc. Choosing simple approach for now.

**D3 — Q4 is a fully-missing canonical interval, not a partial pair.** Both 15-min halves of the
30-min bucket ending `2026-06-17 14:00` SAST are absent, so the partial-pair rule (flag, never sum
the survivor) does not trigger here; the gap rules do (single missing interval → interpolate,
flag `interpolated`). The partial-pair path still gets a unit test — the real data just happens
not to exercise it.

**Sanity shape check** (supports the CLAUDE.md expectation): generation averages ≈ 2000 kWh per
half-hour vs total consumption ≈ 900 kWh (MTR-1001 ≈ 300, MTR-1002 ≈ 200, PT-77 ≈ 780 kW × 0.5 h
≈ 390). The allocation cap will bind almost every interval, residual will be near zero, and
monthly unallocated generation will be large.

## Stage 2 — CLI skeleton, typed config loader, structured logging

**D4 — No packaging.** Not a distributable library, so no `pyproject.toml`. The `etana/` package
sits at the repo root and runs with zero install: `python -m etana run --config
config/allocation_config.json --data ./data --out ./out`. An empty root `conftest.py` puts the
repo root on `sys.path` so pytest imports `etana` the same way. Stdlib-only so far; the dev venv
carries pytest (plus `tzdata` — Windows Python ships no IANA timezone database).

**D5 — TOU windows are parsed from the prose config strings.** `tou_periods_sast` holds prose
("Weekdays 07:00-10:00 and 18:00-20:00"), and nothing may be hard-coded, so the loader extracts
every `HH:MM-HH:MM` range as a weekday window. A bucket with no ranges ("All other times…") is the
**complement**: uncovered weekday hours plus all weekend hours. Coverage of 24h/7d therefore holds
by construction, and the loader validates the two remaining failure modes: overlapping windows and
not-exactly-one complement bucket. The `note` key is documentation, not a bucket. In practice this
would come from an API or DB as structured data rather than prose.

**D6 — Timezone from config, not code.** The `timezone` field is also prose; the leading token
(`Africa/Johannesburg`) is taken as the IANA name and validated via `ZoneInfo` at load. The
canonical grid (Stage 3) takes its zone from here.

**D7 — Allocation percentages not summing to 100 is a finding, not a crash.** The loader validates
each percentage individually (structural), but the total is business data: logged as a warning,
applied exactly as configured, and carried into the data quality report (Stage 7). Rates ↔ TOU
buckets must match exactly (structural: a bucket without a rate cannot be billed).

**Structured logging:** stdlib `logging` with a kv formatter (`level=… event=… key=value`) on
stderr — grep-able runs without a structlog dependency. No `print` anywhere.

## Stage 3 — Canonical grid

**D8 — Pandas adopted for datagrids and numerics** (ethos change, recorded in CLAUDE.md): use
pandas to the greatest extent for grids, resampling, joins and aggregation — drastically fewer
lines than hand-rolled loops. Domain logic stays in plain, directly-testable functions that
operate on pandas structures. The canonical layer is a DataFrame indexed by tz-aware
`interval_end` with columns `meter_id, kwh, flag, source`; the grid is
`month_grid(year, month, tz)` → `pd.DatetimeIndex`, month and zone from config. Note: pandas 3.0
str-dtype columns don't compare elementwise against `(str, Enum)` members, so `QualityFlag` is a
`StrEnum`.

**D9 — `missing` is an explicit QualityFlag value.** The planned enum was
`actual | interpolated | estimated | suspect`, but `to_canonical` must *mark* absent intervals
without deciding their fate — that's the quality engine's call (interpolate / estimate / refuse).
An honest fifth state beats overloading `suspect` or a bare NaN: after gap handling, no billed row
is ever `missing`. Off-grid or duplicated labels at this layer are programming errors (adapters
window and dedupe first) and fail loud rather than being reported as data findings.

## Stage 4 — MeterFlow adapter

**D10 — Dedupe happens at ingestion, in the adapters' shared base.** `to_canonical` demands
unique labels, so last-received-wins runs before landing (CLAUDE.md's "rows in, rows after
dedupe" ingestion logging implies the same). Exact and conflicting duplicates are counted
separately and every conflict is logged individually with both values; Stage 7 lifts these log
events into QualityFinding records. The tie-break (file order = arrival order, last wins) is
policy shared by all providers, hence `base.py`, not per-adapter code.

**D11 — Rows the adapter cannot place are excluded loudly, never crashed on.** An unparseable
timestamp cannot be keyed to any interval: logged row-by-row (`unparseable_timestamp`) and
excluded — the real file has none. A present-but-null kWh value *can* be keyed: it lands on the
grid as an actual-flagged NaN and Stage 7 reports it separately from missing rows. Out-of-window
rows are counted and logged (`rows_excluded_outside_window`).
