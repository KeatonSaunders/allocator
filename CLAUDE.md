# Agent Notes

ABC Energy buys electricity from independent generators and wheels it through the grid to
customer sites. At month end, half-hourly meter data determines how much of each generator's
output was allocated to each customer site, and prices that energy under time-of-use tariffs.

Billing month: **June 2026**. Generator **Riverside Hydro (5 MW)** wheels to three customer sites.

AI coding is accepted, but the follow-up interview tests detailed understanding of the
architecture and efficient, production-grade use of AI coding tools. The assessment tests
software development expertise plus the ability to pick up energy concepts quickly.

## Data (`data/`)

| File | Contents | Interval | Unit | TZ | Convention |
|---|---|---|---|---|---|
| `meterflow_2026-06.csv` | MTR-1001 (Atlantic Foods), MTR-1002 (Cape Textiles) | 30 min | kWh | UTC | interval-ending |
| `powertrack_2026-06.csv` | PT-77 (Delta Cold Storage) | 15 min | avg kW | SAST | interval-beginning |
| `riverside_hydro_generation_2026-06.csv` | Riverside Hydro output | 30 min | kWh | SAST | interval-ending |
| `allocation_config.json` | Allocation percentages, TOU period definitions, wheeling rates | — | — | — | — |

## Goals

- One CLI command produces both deliverables: the data quality report and the monthly summary.
- Keep ingestion, normalisation, allocation and reporting separate. Provider quirks (semicolons,
  kW, UTC) belong in the adapters and must not leak into the allocation core. A new provider is a
  new adapter, not a patch to the engine.
- Domain logic (allocation, TOU classification) as plain functions, independent of I/O, so it can
  be tested directly.
- Everything configurable comes from `allocation_config.json`: sites, allocation percentages, TOU
  windows, rates. Nothing hard-coded.
- Every judgement call the brief leaves open is made explicitly, implemented and recorded in the
  README. Surfacing ambiguity is part of the deliverable.

## Domain rules

- **Canonical form: 30-minute intervals, kWh, SAST, interval-ending.** Every source maps onto that
  one grid. 1440 intervals for June.
- **Allocate per interval, then aggregate.** Per half hour, per site:
  `allocated = min(allocation_pct * generation, consumption)`;
  `residual = consumption - allocated`; `unallocated = generation - sum(allocated)`.
- **Wheeling amount = allocated energy × TOU rate**, not consumption × rate. The summary lists
  both, so state which one was billed on.
- **TOU buckets are classified on SAST local time**, weekday/weekend. Peak: weekdays 07:00–10:00
  and 18:00–20:00. Standard: weekdays 06:00–07:00, 10:00–18:00, 20:00–22:00. Offpeak: everything
  else, all weekend hours included.
- **Public holidays are normal weekdays** — the config says so. June contains a real SA public
  holiday. Do not import a holidays library; it will silently reclassify a day and change the
  invoice.
- **Round only at the end.** Full precision through the calculation; round the final ZAR amounts.
  Never round mid-pipeline.

## Data quirks — standing checks for every meter feed

Real meter data is never clean. Never silently drop, fabricate or overwrite a row. Every quirk
found must be surfaced in the data quality report, not merely handled in code.

**1. Establish the contract for each feed before writing any transform.** State each explicitly,
then assert it in code:
- *Timezone*: UTC or local? Convert to the canonical zone **first**, then window the billing
  month — otherwise the wrong hours get clipped at the boundaries.
- *Interval convention*: beginning or ending? Never join two feeds on raw labels until both are on
  the same convention. Cross-check the documented convention against the actual first/last
  timestamps; if the file spans exactly the month boundary, the docs may be wrong. Pick one
  canonical convention for the whole pipeline and state it.
- *Interval length*: 5/15/30/60 min. Aggregate to canonical length; do not reindex and hope.
- *Units*: kW (average power over the interval) vs kWh (energy). `energy = kW × interval_hours`.
  Also watch Wh/MWh, meter multipliers, CT ratios.
- *Sign convention*: is export negative? Is generation positive?
- *Register type*: interval values, or a cumulative register that must be differenced (handling
  rollover/reset)?
- *File format*: delimiter, decimal separator, date order (DD/MM vs MM/DD), split date/time
  columns, BOM, quoted headers, trailing whitespace.

**2. Completeness.** Compute the expected interval count (`days × 1440 / interval_minutes`) per
meter and reconcile against actual; report the delta. Distinguish missing *rows* from present rows
with null/blank values. Check the first and last intervals of the month explicitly, after timezone
conversion.

**3. Duplicates.** Separate **exact** duplicates (same key, same value) from **conflicting** ones
(same key, different value) — a plain `drop_duplicates()` silently keeps the conflicting pair and
looks like it worked. State a tie-break rule (last-received wins / provider revision number /
max), apply it consistently, and log every conflict individually.

**4. Gaps.** Classify by contiguous length: short isolated gaps and long outages are different
problems and must not share a fill strategy. Linear interpolation for short gaps; profile-based
estimation (same weekday / TOU period average) for longer ones — mean substitution is known to
introduce severe bias. Beyond a stated threshold, invent nothing: flag and escalate. When
aggregating a coarser interval from finer ones, a partially missing target interval must not be
silently summed from the surviving fragment.

**5. Values present but wrong.** Runs of exact zeros (outage, meter fault, or missing-coded-as-
zero? flag either way, do not assume). Frozen readings (identical value repeated). Negatives where
physically impossible. Values above nameplate, CT rating or plausible site load. Step changes and
spikes inconsistent with the site's load profile.

**6. Provenance.** Raw inputs stay immutable; transformations produce new layers. Every canonical
row carries a quality flag (`actual` / `interpolated` / `estimated` / `suspect`) and its source,
carried through to the reporting layer so billed totals can be split by data quality. This could
become important for client queries and idempotent re-runs should the future need arise.

**7. Report the checks that passed.** The report states the invariants verified and held (no
negatives, within nameplate, interval counts reconcile), not only the failures. A check that ran
and passed is evidence; an unstated assumption is not.

**8. Invariants as tests.** Encode reconciliation identities as assertions that run on every
execution, not as comments.

## Quality rules

- Keep the implementation small, sharp, and easy to understand. Try to write elegant, graceful code. 
  Don't settle for the first thing that comes to mind, try to find the most minimal, better working design. 
  Don't introduce slop: for example fragile code that just patches specific cases, dead or useless code 
  and code way more complicated than it needs to be. Treat code as a liability.
- SOLID principles and design patterns are good only where they improve future extension, maintenance or
  legibility. No abstraction for a plethora of hypothetical futures until it is necessary.
- Favour legibility and clarity over unnecessary abstraction; small duplication is acceptable if
  it makes the code far easier to read and reason about.
- Expect future extension: new generators, consumers, allocation strategies.
- **Structural** errors (missing file, unexpected schema, unparseable header) → fail loud and
  early: clear message, non-zero exit.
- **Data** problems → treat as data: report and continue. Never crash on a bad row; never quietly
  discard one.
- Structured logging, not bare prints: rows in, rows after dedupe, intervals expected vs present —
  a run must be observable.
- Comment on domain reasoning, not syntax: why the 0.25, why the `min()`, why this timezone
  handling. Prefer comments beside the implementation over separate design documents.
- No silent defaults on ambiguous inputs. Every decision made is written down in `DECISIONS.md`.

## Testing

Tests are where the tricky parts are proven correct. At minimum:

1. UTC→SAST conversion and month windowing: no dropped boundary hours.
2. kW→kWh (×0.25) and 15→30-minute aggregation.
3. Generator and consumption series land on the same interval grid.
4. The cap: entitlement below, above and equal to consumption.
5. Per-interval invariants: `allocated <= consumption`, `unallocated >= 0`, nothing negative.
6. TOU classification: a boundary interval, a weekend, and the public holiday treated as a
   weekday.
7. Exact vs conflicting duplicates, and missing-interval coverage.

**Sanity check on the real data:** generation substantially exceeds total consumption, so the cap
binds in nearly every interval. Residual grid purchase should be near zero most of the time and
monthly unallocated generation should be large. If the output does not have that shape, something
is wrong.

## Working style

- Spec first: confirm the open decisions, then implement in small steps with a test alongside each
  tricky transform.
- Review every diff. This code gets extended live in an interview; retained understanding is the
  real deliverable, and code I cannot defend line by line is worse than no code.
- Small, meaningful commits. The history should read as a story.

## Commit messages

Conventional prefix (`feat:` / `fix:` / `docs:` / `test:` / `refactor:` / `chore:`), imperative
subject under ~72 characters, body only where the *why* is not obvious from the diff. Reference the
plan stage where it helps the history read as a story:

```
feat: powertrack adapter — kW->kWh, 15->30min, beginning->ending

15-min average kW is converted to kWh (x0.25) before aggregation, and a
30-min bucket missing one of its two 15-min inputs is flagged rather than
summed from the survivor.

Co-Authored-By: Claude <noreply@anthropic.com>
```
