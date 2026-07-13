# Etana — energy wheeling allocation

Etana Energy buys electricity from **Riverside Hydro (5 MW)** and wheels it to three customer
sites. This tool takes the month's half-hourly meter feeds, lands them on one canonical grid,
allocates each half-hour of generation to the sites under their contracted percentages, prices
the allocated energy under time-of-use tariffs, and writes two deliverables:

- **`quality_report.md`** — feed contracts as asserted, interval reconciliation per meter, every
  data quirk found with the action taken, and the checks that ran and passed.
- **`monthly_summary.md`** — per site × TOU period: consumption, allocated energy, residual and
  the wheeling amount in ZAR; site invoice totals; the generation account; and a split of billed
  energy by data quality flag.

Every table in the markdown is also written as its own CSV at full precision — the human and
machine layers are two renderings of the same frames, so they cannot diverge:
`feed_contracts`, `interval_reconciliation`, `findings`, `monthly_summary`, `site_totals`
(including the once-rounded `invoice_zar`), `generation_account`, `billed_by_flag`.

## Run

Requires Python 3.11+:

```bash
pip install -r requirements.txt   # pandas; tzdata on Windows; pytest for the tests
python -m etana run --config config/allocation_config.json --data ./data --out ./out
```

Tests:

```bash
pytest tests/ -v
```

Structural problems — malformed config, missing or ambiguous feed files, a configured meter with
no data — exit non-zero with the offending detail in the message. Data problems never crash the
run: they become findings in the quality report and processing continues.

## Architecture

```
adapters/          provider quirks live here and nowhere else
  meterflow.py     UTC ISO-8601, 30-min kWh, interval-ending, stacked two-meter file
  powertrack.py    SAST DD/MM dates, 15-min avg kW, interval-beginning, semicolons
  generation.py    SAST, 30-min kWh, labels interval-beginning despite docs (D1)
canonical.py       the one grid: 30-min, kWh, SAST, interval-ending, 1440 intervals
quality.py         standing checks -> QualityFinding records; gap policy; flags
tou.py             pure: interval label -> peak/standard/offpeak (config-driven)
allocation.py      pure: per-interval cap, residual, unallocated; invariants assert
report.py          each table built once: CSV verbatim, markdown rendered from
                   the same frame; single ZAR rounding point (invoice_zar)
cli.py             one command wires it together
```

A new provider is a new adapter that lands on `canonical.month_grid`; the engine does not change.
Everything configurable — sites, percentages, TOU windows, rates, timezone, nameplate — comes
from `allocation_config.json`; nothing is hard-coded.

## Decisions register

The full register with reasoning is [`DECISIONS.md`](DECISIONS.md) (D1–D26); the calls that move
money or data:

| Decision | Call |
|---|---|
| Billing window, SAST, interval-ending | `2026-06-01 00:30` … `2026-07-01 00:00`, 1440 intervals; convert timezone **before** windowing |
| Generation feed convention | Documented ending, but the data spans exactly `00:00…23:30` — treated as **beginning**, +30 min (D1) |
| TOU bucket for an interval-ending label | Classified on the interval **start** (label − 30 min): label `07:00` is Standard, not Peak (D19) |
| Conflicting duplicates | Last-received wins (file order = arrival order); every conflict logged with both values (D2/D10) |
| Gap fill thresholds | ≤2 intervals interpolate; 3–12 profile-estimate (same weekday × same slot, trusted rows only); >12 **refuse** — flag suspect, escalate, exclude from billing (D14/D15) |
| Partial 15-min pair → 30-min bucket | Scaled from the survivor and flagged `estimated`; never silently summed (D12) |
| Zero/frozen runs | ≥6 intervals (3 h) of identical actuals → `suspect`, values preserved, cause not assumed (D16) |
| Over-nameplate / negative values | Flagged `suspect`, values preserved — clipping fabricates readings (D13) |
| Wheeling billed on | **Allocated energy × TOU rate**, not consumption; the summary shows both (D22) |
| NaN (refused) intervals | Excluded, not zeroed; exclusions surfaced next to the totals (D20) |
| Public holidays | Normal weekdays per config; no holidays library; Youth Day pinned by test (D19) |
| Rounding | Full precision throughout; ZAR rounded exactly once, into `site_totals.invoice_zar` — the per-site invoice figure (D24/D26) |
| Allocation % not 100 | Finding, applied as configured; but actual over-allocation (negative unallocated) refuses to bill (D7/D21) |

## June 2026 result shape (sanity check)

Generation 2,769,905 kWh vs total consumption 1,765,746 kWh: the cap binds in 81–94% of
intervals per site, residual grid purchase is 3.6% of consumption, and 38.5% of generation is
unallocated — exactly the shape the brief predicts. Total wheeling billed (sum of the three
per-site invoice figures): **ZAR 2,846,534.35**.

## Two questions for the client

1. **The MTR-1002 conflicting re-send (8 June, 10:30 SAST): which value is authoritative?**
   The provider sent `633.423` then `1140.161` for the same interval. Per the stated last-received
   rule we billed `1140.161` — but that value is roughly double the meter's typical reading for
   that hour, so the "correction" may itself be the error. At the peak rate the difference is
   ~ZAR 1,267 on this one interval. Does MeterFlow provide a revision number or correction flag
   we should key on instead of file order?

2. **The generator's 12-hour zero run (15 June, 06:00–18:00 SAST): real outage or
   missing-coded-as-zero?** We billed it as a real outage (zero generation → nothing allocated,
   sites bought ~33,000 kWh of grid power that day, and the intervals are flagged suspect). If the
   plant was actually running and the telemetry failed, the readings should be estimated instead,
   which would materially move both the wheeling total and the unallocated figure. Can Riverside
   Hydro confirm from their operational log?

## Written Questions

**Q2. In production this process runs daily, and providers re-send corrected data for past days, sometimes after the month has been invoiced. How should ingestion and storage be designed to handle this?**

I'd keep every file we receive exactly as it arrived, identified by a hash of its contents, so that re-sending the same file does nothing and every billed number can be traced back to a specific file. Meter readings would be versioned, recording both the interval they describe and when we learned about them. Nothing would get overwritten or deleted: a correction is just a newer version that supersedes the old one. Each allocation run is saved as a snapshot of its inputs, config and code version, so any invoice can be reproduced later. While the month is still open, a correction just triggers a re-run. Once the month has been invoiced, one can't quietly rewrite the invoice — we would have to recompute, compare against what was billed, and raise a credit or debit note in the next cycle if the difference is material. In a redistribution model (Q1), allocation depends on all the meters in a half-hour, and so correcting one meter changes the unallocated total and can change other customers' bills — meaning a correction requires recomputing the whole portfolio's interval, not just that meter, which is a rather sad path indeed.

**Q4. At a high level, explain how you would calculate and present the customer's savings and the trader's margin from energy supplied by the hydro generator?**

The customer's saving is a comparison against what they would otherwise have paid: for each half-hour, take the energy allocated to them and multiply it by the difference between the grid rate they'd have paid in that time-of-use period and what Etana charges them (the PPA rate plus wheeling, losses and levies), then add it up over the month. Only the charges that actually change should be counted, so the energy charge and any avoided network charges, while fixed and demand charges I would imagine stay the same, but would need to confirm with someone more experienced. It must be done half-hourly by time-of-use bucket. The trader's margin is the other side of the same trade: what customers pay for the allocated energy, less the cost of the PPA, the wheeling charges Etana pays on, and losses. My understanding is that Etana typically pays for everything the generator produces but can only bill customers for what gets allocated, and so what happens to that surplus is the dominant driver of margin. One could show the customer a monthly statement (consumption, energy wheeled, energy still bought from the grid, cost with and without Etana, saving in rands and percent, split by TOU), and the trader a portfolio view: rands per kWh generated versus sold, the share of generation actually allocated, and a breakdown from PPA cost to net margin.
