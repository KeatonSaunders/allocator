# Data Quality Report — 2026-06

Every quirk found is listed with the action taken; checks that ran and passed are listed too, because a verified invariant is evidence.

## Feed contracts (as asserted in code)

| provider | timezone | interval_minutes | unit | convention | register | sign | delimiter | decimal |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MeterFlow | UTC | 30 | kwh | ending | interval | positive | , | . |
| PowerTrack | Africa/Johannesburg | 15 | kw | beginning | interval | positive | ; | . |
| RiversideHydro | Africa/Johannesburg | 30 | kwh | beginning | interval | positive | , | . |

## Interval reconciliation per meter

Expected intervals per meter: **1440** (full billing month, 30-min, SAST, interval-ending). `unbilled_null` counts intervals excluded from billing (refused gaps).

| meter | intervals | actual | interpolated | estimated | suspect | unbilled_null | total_kwh |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GEN-RH-01 | 1440 | 1416 | 0 | 0 | 24 | 0 | 2,769,905.090 |
| MTR-1001 | 1440 | 1428 | 0 | 12 | 0 | 0 | 682,826.884 |
| MTR-1002 | 1440 | 1440 | 0 | 0 | 0 | 0 | 501,198.590 |
| PT-77 | 1440 | 1429 | 1 | 0 | 10 | 0 | 581,720.649 |

## Findings (action taken on every one)

| meter | check | severity | detail | action | start | end |
| --- | --- | --- | --- | --- | --- | --- |
| MTR-1002 | duplicates | warning | conflicting duplicate: values [633.423, 1140.161] for the same interval | kept last-received 1140.161 (file order = arrival order) | 2026-06-08 10:30:00+02:00 | 2026-06-08 10:30:00+02:00 |
| GEN-RH-01 | contract_override | warning | feed documented interval-ending, but the raw span (00:00..23:30, exactly the month) proves interval-beginning | labels treated as beginning, shifted +30 min (D1) | — | — |
| GEN-RH-01 | zero_run | warning | value 0.0 repeated for 24 intervals — outage, stuck register or missing-coded-as-zero; cause not assumed | flagged suspect, values preserved | 2026-06-15 06:30:00+02:00 | 2026-06-15 18:00:00+02:00 |
| MTR-1001 | completeness | warning | 1440 intervals expected: 1428 measured, 12 missing rows, 0 present-but-null | reported | 2026-06-01 00:30:00+02:00 | 2026-07-01 00:00:00+02:00 |
| MTR-1001 | gap | warning | 12-interval gap | weekday x slot profile estimate, flagged estimated | 2026-06-12 06:30:00+02:00 | 2026-06-12 12:00:00+02:00 |
| PT-77 | completeness | warning | 1440 intervals expected: 1439 measured, 1 missing rows, 0 present-but-null | reported | 2026-06-01 00:30:00+02:00 | 2026-07-01 00:00:00+02:00 |
| PT-77 | frozen_reading | warning | value 176.0 repeated for 10 intervals — outage, stuck register or missing-coded-as-zero; cause not assumed | flagged suspect, values preserved | 2026-06-25 08:30:00+02:00 | 2026-06-25 13:00:00+02:00 |
| PT-77 | gap | warning | 1-interval gap | linear interpolation, flagged interpolated | 2026-06-17 14:00:00+02:00 | 2026-06-17 14:00:00+02:00 |

## Checks that ran and passed

| meter | check | severity | detail | action | start | end |
| --- | --- | --- | --- | --- | --- | --- |
| config | allocation_pct_total | info | configured allocation percentages sum to 100.0 | applied as configured | — | — |
| MeterFlow | feed_read | info | 2873 data rows read from meterflow_202606.csv | parsed against contract | — | — |
| MeterFlow | duplicates | info | 4 exact duplicate key(s) (same value re-sent) | collapsed — identical, no tie-break needed | — | — |
| PowerTrack | feed_read | info | 2878 data rows read from powertrack_202606.csv | parsed against contract | — | — |
| PowerTrack | duplicates | info | no duplicate (meter, interval) keys | none needed | — | — |
| RiversideHydro | feed_read | info | 1440 data rows read from riverside_hydro_generation_202606.csv | parsed against contract | — | — |
| RiversideHydro | duplicates | info | no duplicate (meter, interval) keys | none needed | — | — |
| GEN-RH-01 | physical_bounds | info | nameplate cap 2500 kWh/interval: 0 over, 0 negative | none needed — within bounds | — | — |
| GEN-RH-01 | completeness | info | 1440 intervals expected: 1440 measured, 0 missing rows, 0 present-but-null | reported | 2026-06-01 00:30:00+02:00 | 2026-07-01 00:00:00+02:00 |
| GEN-RH-01 | month_boundary | info | boundary intervals both present | reported | — | — |
| GEN-RH-01 | gaps | info | no gaps after landing | none needed | — | — |
| MTR-1001 | month_boundary | info | boundary intervals both present | reported | — | — |
| MTR-1001 | value_runs | info | no zero or frozen runs of 6+ intervals | none needed | — | — |
| MTR-1002 | completeness | info | 1440 intervals expected: 1440 measured, 0 missing rows, 0 present-but-null | reported | 2026-06-01 00:30:00+02:00 | 2026-07-01 00:00:00+02:00 |
| MTR-1002 | month_boundary | info | boundary intervals both present | reported | — | — |
| MTR-1002 | value_runs | info | no zero or frozen runs of 6+ intervals | none needed | — | — |
| MTR-1002 | gaps | info | no gaps after landing | none needed | — | — |
| PT-77 | month_boundary | info | boundary intervals both present | reported | — | — |
