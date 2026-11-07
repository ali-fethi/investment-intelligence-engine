# Sprint 2.1 patch (v0.3.2): ROIC transition, point-in-time ERP, TTM lineage

Valuation date: close of 5 Nov 2026. Before = Sprint 2.1 (`ccb55aa`, kept as
`docs/valuation/valuation_2026-11-05_before_patch.json`). After = this patch (`valuation_2026-11-05.json`).

## Result: implied revenue growth, years 1–5

| | GOOGL | MSFT | META |
|---|---:|---:|---:|
| Before (Sprint 2.1 base) | 21.17% | 22.54% | 20.09% |
| ERP: year-end 2025 (4.23%) → October 2026 monthly (3.70%) | −2.11 pts → 19.07% | −1.90 pts → 20.64% | −2.36 pts → 17.73% |
| ROIC fade, years 6–10 | +1.43 pts | +1.41 pts | +1.31 pts |
| **After (v2.1 base)** | **20.50%** | **22.05%** | **19.04%** |
| WACC, before → after | 9.63% → 9.03% | 9.25% → 8.70% | 10.70% → 9.97% |

These figures were recomputed by `audit/independent_check.py` from the raw source files. It has its own
DCF (explicit ROIC arrays and a secant solver) and its own ERP reader (openpyxl, Wayback served-capture
dates). For each company the maximum relative difference from the engine is 9×10⁻¹⁶ (GOOGL), 3×10⁻¹⁵
(MSFT) and 4×10⁻⁶ (META). The META difference comes from a $1m rounding difference between quarterly
and year-to-date revenue in XBRL. It was present in Sprint 2.1, when "identical to machine precision" was
overstated for META.

## 1. Forecast ROIC vs terminal RONIC

- **Inconsistency:** in years 1–10, each dollar of reinvestment earns m(1−τ)·S2C. That is 25.6% (GOOGL),
  29.6% (MSFT) and 27.8% (META). From year 11 it earned RONIC = WACC (9.0–10.0%), a one-step drop in the
  return on new capital.
- **Transition (new base):** incremental ROIC stays at the forecast level through year 5, then fades
  linearly to RONIC = WACC by year 10. Sales-to-capital follows it (GOOGL 0.94 → 0.33 by year 10). The
  fade runs alongside the growth fade, so excess returns end as the growth advantage ends, with no step at
  year 11. It costs more reinvestment, so the price requires 1.3–1.4 pts more growth.

Explicit alternatives:

| Scenario | GOOGL | MSFT | META |
|---|---:|---:|---:|
| A. Abrupt drop at year 11 (Sprint 2.1 base, October ERP) | 19.1% | 20.6% | 17.7% |
| **B. Fade to RONIC = WACC (base)** | **20.5%** | **22.1%** | **19.0%** |
| C. Fade to half the spread (RONIC 17.3% / 19.1% / 18.9%) | 15.8% | 16.8% | 15.1% |
| D. Forecast ROIC forever (25.6% / 29.6% / 27.8%) | 14.3% | 15.5% | 13.8% |

B is the conservative, internally consistent case. A is inconsistent. C and D assume the excess returns
persist in perpetuity: they cut the requirement by 5–7 pts, more than any other single assumption.

## 2. ERP: point-in-time and definition-consistent

- **Gap:** Sprint 2.1 used the end-2025 annual value (4.23%) for a November 2026 valuation. It was implied
  at a 4.18% Treasury rate and an S&P level 10% lower, so it is stale against the 4.84% r_f of 5 Nov.
- **Definition:** Damodaran's monthly `ERPbymonth.xlsx`, column "ERP (T12m)", is the annual "Implied ERP
  (FCFE)" method. They are identical at every year-end except three:
  - 2011: 6.01% annual vs 7.32% monthly;
  - 2012: 5.78% vs 6.00%;
  - 2020: 4.72% vs 4.20%, where the annual figure departs from the trailing-cash-flow basis.
  
  The monthly series is the one definition used consistently across time. The other monthly columns
  (sustainable payout, smoothed, normalised, default-adjusted r_f) are different methods and are not used.
- **Point-in-time:** 24 vintages were read (23 Wayback captures from Nov 2021, plus the current file).
  - A month's value is available from the first capture that shows it (verified).
  - When the first evidence comes more than 45 days late, availability is assumed at the start of the
    month + 14 days and flagged.
  - At 5 Nov 2026 the base uses **October 2026 = 3.70%**, captured 21 Oct (verified). November (3.79%)
    was first seen on 7 Nov and is not used.
  - No published value was revised across vintages.
- **Unverifiable vintages:**
  - All months before Nov 2021 (first capture): 183 of the 250 historical v2.1 rows rely on an assumed
    publication date.
  - Annual values for 2022 and earlier: the first parsable `histimpl.xls` capture is March 2023.
  - The annual end-2025 value appeared between 7 and 15 Jan 2026, so the assumed 10 January is within
    that window but unconfirmed. End-2024 appeared by 30 Jan 2025.

## 3. TTM revenue lineage

`inputs_at()` wrote both TTM revenue figures (current and 3 years earlier) to `sources["ttm_revenue"]`.
The later write won, so the recorded lineage pointed to FY2023Q3 facts with 2025 availability dates.
Values and implied growth were unaffected (only metadata), and `inputs_available_at` was unchanged.

Each TTM now has its own entry, with its period and fiscal quarters. For example, GOOGL "TTM to FY2026Q3",
observations 18761…18753, available 2026-10-29T00:29:06Z.

Regression tests: `test_ttm_lineage_not_overwritten_by_three_years_ago` (synthetic) and
`test_ttm_lineage_matches_calculation_period_real_db`. Both fail on the old code and pass on the new.

The audit script checks the lineage against raw companyfacts:
- after the patch, every company's recorded window ends 2026-09-30, the latest period filed;
- before the patch, the recorded window ended 2023-09-30.

## 4. Historical series

All 250 computable pre/post-release rows were recomputed (`rdcf21|…`). Against v2 the mean change is
+0.75 pts (range −3.1 to +6.9). The same 74 rows remain not computed (Alphabet depreciation before 2021).

## 5. Unchanged

- Sprint 1 snapshots: 3/3 rebuild hashes identical.
- Event Reaction Engine cases: 6/6 identical, except the code version and content-hash fields.
- QA: 0 failures. Look-ahead: 0. Tests: 77/77, including network.

## 6. External reproduction

Run `audit/make_bundle.py`; the bundle README has the commands. The bundle contains 28 documents:
- SEC companyfacts;
- the Treasury CSV;
- the ALFRED T10YIE vintage;
- served-capture records;
- 30 XBRL observation rows.

The other 30 documents are listed by URL and sha256, and `fetch_restricted.py` re-downloads them. They are
Yahoo, Moody's Aaa via FRED, and the Damodaran files, including their Wayback copies.

Reproduced from an empty folder:
- all 23 Wayback copies and both current Damodaran files were byte-identical;
- Yahoo's bytes had changed, as expected;
- the results were still identical.
