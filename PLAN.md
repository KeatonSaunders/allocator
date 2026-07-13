# Development Plan — Energy Wheeling & Allocation

Companion to `CLAUDE.md`. Ten stages, each a small commit with a test alongside the tricky part.
Stages 1–3 come first deliberately: they prevent the common failure of writing the allocation
engine before discovering the feeds were never on the same interval grid.

**Rule for every stage:** the diff must be reviewable in one sitting and explainable line by line.
Code that cannot be explained is worse than no code — cut it back rather than keep it.

---

## Stage 0 — Ground rules

Budget 4–6h: Stages 1–3 ≈ 1h, 4–6 ≈ 1.5h, 7–9 ≈ 1.5h, 10 ≈ 1h.

Target layout — deliberately small, not to be added to without cause:

```
src/etana/
  config.py         # load + validate allocation_config.json, fail loud
  canonical.py      # canonical row schema, QualityFlag, the 30-min SAST grid
  adapters/
    base.py         # MeterAdapter protocol + FeedContract
    meterflow.py    # UTC, 30-min kWh, interval-ending
    powertrack.py   # SAST, 15-min avg kW, interval-beginning
    generation.py   # SAST, 30-min kWh, interval-ending
  quality.py        # checks -> QualityFinding records (failures AND passes)
  tou.py            # pure: timestamp -> bucket
  allocation.py     # pure: per-interval cap, residual, unallocated
  report.py         # data quality report + monthly summary writers
  cli.py            # one command, both deliverables
tests/
DECISIONS.md        # working scratchpad -> folded into README at Stage 10
```

Three abstractions, not thirty: **Adapter** (provider quirks), **canonical frame** (the one grid),
**pure domain functions** (TOU + allocation). Everything else is a function. May look to add a 
strategy pattern for data handing policies if time allows.

---

## Stage 1 — Recon and contracts (no pipeline code yet)

Establish what is actually in the files before writing a single transform. Throwaway inspection
script (`scratch/inspect.py`, gitignored or deleted before submission). Per CSV, record in
`DECISIONS.md`:

- Delimiter, decimal separator, BOM, quoted headers, trailing whitespace, date format.
- First and last raw timestamp, verbatim.
- Row count vs expected count for the stated interval length.
- Any non-numeric or blank value cells.
- Generator: max value vs the 5 MW nameplate (a 30-min interval cannot exceed 2500 kWh).

Then write each feed's **contract** as a `FeedContract` to be asserted in code: timezone, interval
convention, interval length, unit, sign convention, register type (interval vs cumulative).

**Done when:** `DECISIONS.md` holds a filled contract table for all three feeds and states the
exact SAST timestamps of interval #1 and #1440.

**Commit:** `docs: feed contracts and observed file characteristics`

---

## Stage 2 — Skeleton, config, logging, CLI shell

A runnable `etana run --config allocation_config.json --data ./data --out ./out` that loads
config, logs a structured start line, and exits 0 doing nothing else.

- `config.py`: parse into typed objects (sites, allocation pct, TOU windows, rates). **Validate on
  load**: every site has a meter, percentages parse, TOU windows cover 24h without overlap, rates
  exist for every bucket. Missing key or bad shape → clear message, non-zero exit. Nothing about
  sites, rates or TOU boundaries is ever a literal in the code.
- Structured logging (`structlog`, or stdlib `logging` with a JSON/kv formatter). No `print`.
- Assert and log `sum(allocation_pct)`. Not 100% is a *finding*, not a crash — record and continue.

**Test:** the loader rejects a malformed config with a non-zero exit and a useful message.

**Commit:** `feat: cli skeleton, typed config loader, structured logging`

---

## Stage 3 — The canonical grid

The one grid every feed must land on.

- `canonical.py` builds the June 2026 SAST 30-min interval-ending index: **1440 intervals,
  `2026-06-01 00:30` … `2026-07-01 00:00`** — the interval *ending* at midnight on 1 June belongs
  to May; the one ending at midnight on 1 July is June's last. That reasoning goes in a comment; it
  is a commonly botched boundary.
- Canonical row: `meter_id, interval_end (tz-aware SAST), kwh, quality_flag, source`, with
  `QualityFlag = actual | interpolated | estimated | suspect`.
- `to_canonical(df, contract)` reindexes onto the grid and **marks** missing intervals rather than
  dropping or zero-filling them.

**Test:** exactly 1440 entries, strictly monotonic, none duplicated or skipped. South Africa
observes no DST, so no fold/gap handling is required — stated in a comment so the absence of DST
logic is a recorded conclusion, not an omission.

**Commit:** `feat: canonical 30-min SAST interval grid`

---

## Stage 4 — Adapter: meterflow (the timezone feed)

MTR-1001 and MTR-1002 onto the canonical grid.

- File-format quirks from Stage 1 (delimiter, decimal separator, whitespace) live **here and
  nowhere else**.
- Parse timestamps as UTC-aware, convert to `Africa/Johannesburg`, **then** window to June.
- Long/stacked file (two meters) — split by meter id; do not assume ordering.

**Tests:** (1) a UTC timestamp at the month boundary converts to the right SAST interval and is not
dropped by the window; (2) rows outside the June SAST window are excluded and the excluded count
logged; (3) each meter lands on the canonical grid.

**Commit:** `feat: meterflow adapter with UTC->SAST conversion`

---

## Stage 5 — Adapter: powertrack (the unit + resample feed)

PT-77 onto the canonical grid. Three transforms, in this order, each commented:

1. **Convention:** interval-beginning → interval-ending (`+15 min`), *before* any join or
   aggregation.
2. **Unit:** average kW over 15 min → kWh via `× 0.25`. Comment *why* 0.25 (energy = power × hours;
   15 min = 0.25 h).
3. **Length:** sum pairs of 15-min kWh into the 30-min interval-ending bucket.

**The rule that matters here:** a 30-min target built from only one of its two 15-min inputs must
**not** be silently summed from the survivor. Flag it (`suspect` or `estimated`, depending on the
fill strategy) and report it. A naive `resample('30min').sum()` halves the reading and reports
nothing.

**Tests:** kW→kWh on a known value; aggregation of a full pair; a partial pair flagged rather than
silently halved; PT-77 lands on the same index as MTR-1001.

**Commit:** `feat: powertrack adapter — kW->kWh, 15->30min, beginning->ending`

---

## Stage 6 — Adapter: generation

Riverside Hydro onto the canonical grid. Nominally already canonical — asserted, not assumed.

- Assert 30-min, kWh, SAST, interval-ending against the contract.
- Physical bounds: nameplate 5 MW → a 30-min interval cannot exceed **2500 kWh**. Anything above is
  `suspect` and reported, never clipped silently.
- Negatives are physically impossible for this generator → `suspect`.

**Test:** an over-nameplate value is flagged, not clipped or dropped.

**Commit:** `feat: generation adapter with nameplate and sign validation`

---

## Stage 7 — Data quality engine

`quality.py` runs the standing checks over every canonical feed, returning
`QualityFinding(meter, check, severity, interval_range, detail, action_taken)`. In order:

- **Completeness:** expected vs actual interval count per meter; missing *rows* vs present rows
  with null/blank values, reported separately; explicit check of the month's first and last
  interval.
- **Duplicates:** **exact** (same key, same value → safe to collapse) vs **conflicting** (same key,
  different value → tie-break required). Rule: *last-received wins*, since providers re-send
  corrections. Applied consistently; **every conflict logged individually with both values**.
- **Gaps**, classified by contiguous length; thresholds stated in the README, each justified in one
  line, and no mean substitution:
  - 1–2 intervals → linear interpolation, flag `interpolated`
  - 3–12 intervals → profile estimate (same weekday × same TOU period mean for that meter), flag
    `estimated`
  - \> 12 intervals (6h) → **no data invented**: leave null, flag `suspect`, escalate in the report,
    exclude from billed totals with the exclusion made visible.
- **Present-but-wrong:** runs of exact zeros, frozen readings (same value repeated N+ intervals),
  impossible negatives, values above nameplate/plausible site load, step changes. Flag; do not
  assume the cause.
- **Passed checks:** the report also lists invariants verified and held. A check that ran and passed
  is evidence.

Every canonical row carries its `quality_flag` and `source` from here to the reporting layer, so
billed totals can be split by data quality.

**Tests:** exact vs conflicting duplicates handled differently and both reported; a short gap
interpolates while a long gap is refused and escalated; a missing-interval scenario appears in the
completeness reconciliation.

**Commit:** `feat: data quality checks with findings model and quality flags`

---

## Stage 8 — TOU classification (pure)

`tou.py` — one pure function, no I/O, driven entirely by config.

- Buckets from config: Peak (weekdays 07:00–10:00, 18:00–20:00), Standard (weekdays 06:00–07:00,
  10:00–18:00, 20:00–22:00), Offpeak (everything else + all weekend hours).
- **The judgement call:** rows are labelled interval-*ending*. The interval labelled `07:00` covers
  06:30–07:00 and is therefore **Standard**, not Peak. Classify on the interval **start**
  (`label − 30 min`). Recorded in the README; it moves real money.
- **Public holidays are normal weekdays.** June 2026 contains Youth Day (Tue 16 June) — already a
  weekday, so no reclassification. **No holidays library is imported**, and a test pins 16 June to
  weekday buckets so a future dependency cannot silently change the invoice.

**Tests:** boundary intervals (07:00 → Standard, 07:30 → Peak), a weekend interval, and 16 June 2026
as a normal weekday.

**Commit:** `feat: TOU classification on interval-start, SAST, holidays as weekdays`

---

## Stage 9 — Allocation core (pure)

`allocation.py` — plain functions over the canonical frame, no I/O, directly testable. Per interval,
per site:

```
entitlement = allocation_pct * generation      # site's share of this half-hour's output
allocated   = min(entitlement, consumption)    # cannot wheel more than the site used
residual    = consumption - allocated          # bought from the grid
unallocated = generation - sum(allocated)      # generator output with nowhere to go
```

The `min()` carries its reason in a comment: energy delivered cannot exceed energy consumed in the
same half-hour, because there is no storage in this model.

- **Wheeling amount = allocated energy × TOU rate**, not consumption × rate. The summary shows both
  consumption and allocated, and states which one was billed on.
- **Full precision throughout; round only the final ZAR amounts.**
- Aggregate to per-site × per-TOU-bucket: consumption, allocated, residual, wheeling amount — plus
  the month's total unallocated generation.

**Invariants, as assertions that run on every execution:** `allocated <= consumption`,
`allocated <= entitlement`, `unallocated >= 0`, no negatives anywhere,
`consumption == allocated + residual`, `sum(allocated) + unallocated == total_generation`.

**Tests:** entitlement below / above / equal to consumption; the per-interval invariants; zero
generation; zero consumption.

**Commit:** `feat: per-interval allocation with cap, residual and unallocated`

---

## Stage 10 — Reports, sanity check, README

One command produces both deliverables. `report.py` writes:

- **Data quality report** (Markdown + findings CSV): per feed — the contract as asserted, rows in /
  rows after dedupe / intervals expected vs present, every finding with the action taken, **and the
  invariants verified and held**.
- **Monthly summary** (Markdown + CSV): per site × TOU bucket — consumption kWh, allocated kWh,
  residual kWh, wheeling amount ZAR; site totals; total unallocated generation for the month; and a
  split of billed energy by quality flag (actual / interpolated / estimated).

**README** contains run instructions, the decisions register from `DECISIONS.md` (every judgement
call, the call made, and why — non-exhaustive):

| Decision | Call |
|---|---|
| Billing window in SAST, interval-ending | `2026-06-01 00:30` … `2026-07-01 00:00`, 1440 intervals |
| TOU bucket for an interval-ending label | Classify on interval **start** |
| Conflicting duplicates | Last-received wins; every conflict logged individually |
| Gap fill thresholds | ≤2 interpolate, ≤12 profile-estimate, >12 refuse and escalate |
| Partial 15-min pair → 30-min | Flagged, never silently summed from the survivor |
| Wheeling billed on | Allocated energy, not consumption |
| Over-nameplate / negative values | Flagged `suspect`, never clipped |
| Public holidays | Normal weekdays; no holidays library |
| Rounding | Full precision throughout; round final ZAR only |
| Allocation % not summing to 100 | Reported as a finding; applied as configured |

Plus:

- **Two written questions.**

**Commit:** `feat: data quality report and monthly summary; docs: README with decisions`

---

## Definition of done

- [ ] One CLI command produces both deliverables.
- [ ] Provider quirks (semicolons, kW, UTC, interval-beginning) exist only in adapters.
- [ ] Allocation and TOU are pure functions with direct tests.
- [ ] Nothing about sites, percentages, TOU windows or rates is hard-coded.
- [ ] No row silently dropped, fabricated or overwritten anywhere.
- [ ] Every open decision is written down in the README.
- [ ] The output has the expected shape: cap binds, residual near zero, unallocated large.
- [ ] Commit history reads as a story.
- [ ] The design can be explained end to end without reference to the code.
