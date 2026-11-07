Knowledge time 2026-11-07T16:29:27Z.

# Consensus / EPS comparability and statistical quality

## Reconciliation: from 133 releases to the comparable sample

| Step | Releases |
|---|---:|
| releases | 133 |
| with dated consensus EPS (A/B, ≤150 d) | 116 |
| by age bucket | 0-7d: 86, 8-30d: 16, 31-60d: 9, 61-150d: 5 |
| with street actual (vendor) | 91 |
| street actual source | history block: 43, old-layout 'Last Quarter's Earnings': 48 |
| comparability S (street vs street) | 91 |
| …of which GAAP = street (G=) | 64 |
| …of which GAAP ≠ street (X) | 22 |
| comparability G? (GAAP only, basis unverified) | 25 |
| no consensus | 17 |
| consensus but no actual | 0 |
| with measured reaction among S | 86 |

## Data issues

| Issue | Releases |
|---|---:|
| consensus captures disagree | 12 |
| gross outlier: actual/consensus outside [0.5, 2] | 2 |
| mixed provider: consensus not FactSet, street actual FactSet | 15 |
| stale consensus | 14 |

## GAAP vs street EPS (where the vendor's street actual is known and differs)

| Ticker | Quarter | GAAP | Street | GAAP − street |
|---|---|---:|---:|---:|
| MSFT | FY2016Q2 | 0.62 | 0.78 | -0.16 |
| MSFT | FY2016Q3 | 0.47 | 0.62 | -0.15 |
| MSFT | FY2016Q4 | 0.39 | 0.69 | -0.30 |
| MSFT | FY2017Q1 | 0.72 | 0.76 | -0.04 |
| MSFT | FY2017Q2 | 0.66 | 0.83 | -0.17 |
| MSFT | FY2017Q3 | 0.61 | 0.73 | -0.12 |
| MSFT | FY2017Q4 | 0.83 | 0.98 | -0.15 |
| MSFT | FY2018Q2 | -0.82 | 0.96 | -1.78 |
| MSFT | FY2021Q3 | 2.03 | 1.95 | +0.08 |
| MSFT | FY2022Q1 | 2.71 | 2.27 | +0.44 |
| MSFT | FY2023Q2 | 2.20 | 2.32 | -0.12 |
| MSFT | FY2026Q1 | 3.72 | 4.13 | -0.41 |
| GOOGL | FY2016Q1 | 6.02 | 7.50 | -1.48 |
| GOOGL | FY2016Q2 | 7.00 | 8.42 | -1.42 |
| GOOGL | FY2016Q3 | 7.25 | 9.06 | -1.81 |
| GOOGL | FY2016Q4 | 7.56 | 9.36 | -1.80 |
| GOOGL | FY2018Q2 | 4.54 | 11.75 | -7.21 |
| GOOGL | FY2021Q4 | 30.69 | 27.99 | +2.70 |
| META | FY2016Q1 | 0.52 | 0.77 | -0.25 |
| META | FY2016Q2 | 0.71 | 0.97 | -0.26 |
| META | FY2016Q3 | 0.82 | 1.09 | -0.27 |
| META | FY2017Q4 | 1.44 | 2.21 | -0.77 |

## Surprise vs market-model abnormal return on d0

12 samples are reported; with this many looks, a nominal p < 0.05 is expected by chance in about one sample even if there were no relation.

| Sample | n | Spearman | 95% CI (bootstrap) | 95% CI (clustered by reaction week) | Pearson | Sign agreement | Permutation p |
|---|---:|---:|---|---|---:|---:|---:|
| S: street vs street (primary) | 86 | +0.33 | [+0.10, +0.52] | [+0.12, +0.52] (53 weeks) | +0.26 | 66% | 0.002 |
| S, consensus ≤7 days old | 62 | +0.24 | [-0.03, +0.48] | [-0.02, +0.48] (42 weeks) | +0.14 | 63% | 0.056 |
| S, consensus ≤30 days old | 73 | +0.24 | [-0.00, +0.46] | [-0.01, +0.48] (49 weeks) | +0.14 | 64% | 0.050 |
| S, FactSet consensus only (same provider as actual) | 71 | +0.38 | [+0.15, +0.57] | [+0.13, +0.58] (44 weeks) | +0.34 | 68% | 0.002 |
| S, excluding high attribution uncertainty (macro on d0) | 73 | +0.42 | [+0.18, +0.61] | [+0.20, +0.60] (49 weeks) | +0.35 | 71% | 0.000 |
| S, excluding top 5% |CAR| | 80 | +0.34 | [+0.10, +0.55] | [+0.12, +0.53] (53 weeks) | +0.36 | 66% | 0.003 |
| S, winsorised 5/95 (surprise and CAR) | 86 | +0.33 | [+0.11, +0.52] | — | +0.33 | 66% | 0.002 |
| G?: GAAP vs consensus, basis unverified | 25 | +0.08 | [-0.37, +0.54] | [-0.37, +0.53] (16 weeks) | -0.05 | 52% | 0.688 |
| S ∪ G?: all releases with consensus and any actual | 111 | +0.28 | [+0.08, +0.46] | [+0.10, +0.46] (63 weeks) | +0.19 | 63% | 0.003 |
| S, GOOGL only | 32 | +0.36 | [-0.01, +0.65] | [-0.02, +0.65] (32 weeks) | +0.40 | 81% | 0.037 |
| S, META only | 28 | +0.33 | [-0.05, +0.65] | [-0.06, +0.65] (28 weeks) | +0.22 | 64% | 0.090 |
| S, MSFT only | 26 | +0.28 | [-0.10, +0.59] | [-0.10, +0.60] (26 weeks) | +0.18 | 50% | 0.165 |
