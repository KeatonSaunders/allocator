# Monthly Summary — 2026-06

Generator: **Riverside Hydro** (GEN-RH-01, 5.0 MW). **Billing basis: wheeling is charged on ALLOCATED energy × TOU rate — not on consumption.** Consumption is shown for reference; residual (consumption − allocated) is grid purchase, not billed here.

## Per site × TOU period

| meter | site | tou | consumption_kwh | allocated_kwh | residual_kwh | excluded_intervals | rate_zar_per_kwh | wheeling_zar |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MTR-1001 | Atlantic Foods | peak | 141,658.387 | 133,383.516 | 8,274.872 | 0 | 2.5 | 333,458.79 |
| MTR-1001 | Atlantic Foods | standard | 325,737.846 | 299,270.034 | 26,467.813 | 0 | 1.8 | 538,686.06 |
| MTR-1001 | Atlantic Foods | offpeak | 215,430.650 | 215,430.650 | 0.000 | 0 | 1.1 | 236,973.72 |
| MTR-1002 | Cape Textiles | peak | 111,336.016 | 107,050.654 | 4,285.363 | 0 | 2.5 | 267,626.63 |
| MTR-1002 | Cape Textiles | standard | 252,549.340 | 239,404.117 | 13,145.223 | 0 | 1.8 | 430,927.41 |
| MTR-1002 | Cape Textiles | offpeak | 137,313.234 | 137,313.234 | 0.000 | 0 | 1.1 | 151,044.56 |
| PT-77 | Delta Cold Storage | peak | 89,943.784 | 87,341.667 | 2,602.117 | 0 | 2.5 | 218,354.17 |
| PT-77 | Delta Cold Storage | standard | 205,963.392 | 197,328.919 | 8,634.473 | 0 | 1.8 | 355,192.05 |
| PT-77 | Delta Cold Storage | offpeak | 285,813.472 | 285,700.884 | 112.588 | 0 | 1.1 | 314,270.97 |

## Site totals

`invoice_zar` is the pipeline's single ZAR rounding point: rounded once from the site's full-precision sum, so it may differ from summing displayed per-bucket cells by a cent.

| meter | site | consumption_kwh | allocated_kwh | residual_kwh | wheeling_zar | invoice_zar |
| --- | --- | --- | --- | --- | --- | --- |
| MTR-1001 | Atlantic Foods | 682,826.884 | 648,084.199 | 34,742.684 | 1,109,118.56 | 1,109,118.56 |
| MTR-1002 | Cape Textiles | 501,198.590 | 483,768.005 | 17,430.585 | 849,598.60 | 849,598.60 |
| PT-77 | Delta Cold Storage | 581,720.649 | 570,371.470 | 11,349.179 | 887,817.19 | 887,817.19 |

Total wheeling billed: **ZAR 2,846,534.35**.

## Generation account

| generation_kwh | allocated_kwh | unallocated_kwh | unallocated_pct |
| --- | --- | --- | --- |
| 2,769,905.090 | 1,702,223.673 | 1,067,681.417 | 38.5% |

## Billed (allocated) energy by data quality flag

Interpolated/estimated energy is billed but traceable; suspect energy is billed on meter readings that carry an open finding.

| meter | site | actual_kwh | interpolated_kwh | estimated_kwh | suspect_kwh |
| --- | --- | --- | --- | --- | --- |
| MTR-1001 | Atlantic Foods | 639,308.226 | 0.000 | 8,775.973 | 0.000 |
| MTR-1002 | Cape Textiles | 483,768.005 | 0.000 | 0.000 | 0.000 |
| PT-77 | Delta Cold Storage | 568,169.985 | 441.485 | 0.000 | 1,760.000 |

*kWh cells are displayed at 3 dp; every table above is also written as a CSV at full precision.*
