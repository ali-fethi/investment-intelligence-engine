# Reverse DCF: audit of v1, methodology of v2.1, and valuation bridge (Sprint 2.1 + patch v0.3.2)

> **Patch v0.3.2** ([SPRINT_2_1_PATCH.md](SPRINT_2_1_PATCH.md)): incremental ROIC now fades to RONIC over years
> 6–10; the ERP is the latest point-in-time monthly value; TTM lineage fixed. Figures below are v2.1.

Valuation date: close of **5 Nov 2026** (inputs point-in-time). Code: `src/iie/expect/valuation.py`.
Output: `docs/valuation/valuation_2026-11-05.json`. Independent reproduction: `audit/independent_check.py`.

## 1. What v1 calculated, and why it was not defensible

v1 (`implied.py`, Sprint 2) solved for the constant 10-year growth `g` of FCF such that
PV(FCF₀(1+g)ᵗ) + terminal value = market capitalisation, with:

| v1 choice | Problem | Status in v2 |
|---|---|---|
| FCF₀ = operating cash flow − capex | Operating cash flow adds back stock-based compensation ($12–31bn a year here), a real cost to shareholders. During a capex build-out the base is depressed: Alphabet's TTM FCF was $19.7bn with capex at 89% of operating cash flow. Implied growth from a depressed base is mechanically extreme (37%) and unstable | Replaced by NOPAT (GAAP operating income, in which SBC is already an expense) less reinvestment tied to growth |
| Equity value (market cap) discounted at r_f + ERP | Mixes a levered cash flow with an unlevered-style rate. Beta is ignored, the ERP is unsourced (a 4/5/6% grid), and net cash/debt and leases are ignored | Enterprise basis: FCFF at a sourced WACC vs EV = market cap + debt + finance leases − cash and short-term investments |
| Share count = diluted weighted average | Not the count at the valuation date | Cover-page count where tagged (MSFT). Otherwise diluted weighted average, flagged: +0.6% (GOOGL) and +0.7% (META) vs the 10-Q cover page, as checked independently |
| Terminal growth fixed at 3% with no reinvestment | Perpetual growth with zero reinvestment overstates the terminal value | g_T = min(r_f, breakeven inflation + 1% real), with reinvestment g_T/RONIC |
| Constant growth for 10 years | Implausible for the largest companies | Growth g for years 1–5, linear fade to g_T by year 10 |

## 2. v2 model (FCFF, enterprise value)

- R_t = R_{t−1}(1+g_t): g for t = 1–5, then a linear fade to g_T at t = 10.
- FCFF_t = R_t·m·(1−τ) − (R_t − R_{t−1})/S2C_t.
- S2C_t = S2C for t = 1–5. Then incremental ROIC_t = m(1−τ)·S2C_t fades linearly from m(1−τ)·S2C to RONIC by
  t = 10, so returns on new capital converge as growth converges (no step at year 11).
- TV = R₁₀(1+g_T)·m·(1−τ)·(1 − g_T/RONIC)/(WACC − g_T).
- EV = Σ FCFF_t/(1+WACC)ᵗ + TV/(1+WACC)¹⁰.
- g is solved such that EV + cash − debt − leases = price × shares.

| Input | Source and timing | GOOGL | MSFT | META |
|---|---|---:|---:|---:|
| Risk-free rate | 10-year Treasury par yield, Treasury CSV, 5 Nov 2026 | 4.84% | 4.84% | 4.84% |
| ERP | Damodaran monthly implied ERP, "ERP (T12m)" (same method as the annual FCFE series), October 2026, first captured 21 Oct 2026 | 3.70% | 3.70% | 3.70% |
| Beta | OLS, 104 weekly total returns vs SPY (Nov 2024 – Nov 2026); Blume-adjusted | 1.26 → **1.17** (s.e. 0.19) | 1.11 → **1.07** (0.17) | 1.68 → **1.45** (0.21) |
| Cost of equity | r_f + β·ERP | 9.18% | 8.80% | 10.21% |
| Pre-tax cost of debt | Moody's Aaa (FRED AAA), Sept 2026 average (October not yet published at the date) | 6.03% | 6.03% | 6.03% |
| Tax rate | TTM provision / TTM pre-tax income, bounded to [10%, 30%] | 18.1% | 19.7% | 4.6% → **10%** (bounded) |
| Weight of debt | Book debt + finance leases over (that + market cap) | 3.6% | 2.6% | 5.1% |
| **WACC** | | **9.03%** | **8.70%** | **9.97%** |
| Operating margin (TTM GAAP) | XBRL | 33.3% | 47.1% | 32.9% |
| Sales-to-capital | Δrevenue over 3 fiscal years / Σ(capex − depreciation) | 0.94 (FY23–25) | 0.78 (FY24–26) | 0.94 (FY23–25) |
| Incremental ROIC, years 1–5 | m(1−τ)·S2C; fades to RONIC = WACC by year 10 | 25.6% | 29.6% | 27.8% |
| Terminal growth | min(r_f, 10-year breakeven 2.26% + 1%) | 3.26% | 3.26% | 3.26% |
| EV | market cap + debt + leases − cash and short-term investments | $3,360bn | $4,022bn | $1,558bn |
| **Implied revenue growth, years 1–5** | | **20.5%** | **22.1%** | **19.0%** |
| Trailing 3-year revenue growth (for comparison) | | 16.6% | 16.6% | 23.9% |

**Excluded and flagged:**
- Alphabet's non-marketable equity investments (~$135bn in the 10-Q). Including them would lower its implied growth.
- Meta's finance-lease liabilities, which are not tagged and not found in the 10-Q by the independent check.
- Microsoft's $71.0bn of finance leases *are* included as debt.

## 3. Valuation bridge, v1 → v2 (implied growth after each change)

Steps 0–3 express the price as constant 10-year **FCF growth**. Steps 4–5 express it as **revenue growth in
years 1–5**: the cash-flow base itself changes at step 4, so the two metrics are not comparable across that
line.

| Step | Change | GOOGL | MSFT | META |
|---|---|---:|---:|---:|
| 0 | v1: FCFE (CFO − capex), equity value, r = r_f + 5%, terminal 3% | 36.9% | 23.0% | 20.3% |
| 1 | CAPM cost of equity (β × Damodaran ERP) | 36.8% | 21.8% | 23.0% |
| 2 | Deduct SBC from the cash flow | **n/m** (FCF −$10.8bn) | 25.0% | 65.4% |
| 3 | Enterprise basis: FCFF at WACC vs EV | **n/m** | 24.1% | 53.4% |
| 4 | Replace the FCF base with NOPAT − growth-linked reinvestment (capex normalised through S2C) | 15.6% | 16.9% | 15.4% |
| 5 | Terminal g_T anchored, with terminal reinvestment (RONIC = WACC): Sprint 2.1 base | 21.2% | 22.5% | 20.1% |
| 6 | ERP: latest point-in-time monthly value (3.70%) instead of end-2025 (4.23%) | 19.1% | 20.6% | 17.7% |
| 7 | Incremental ROIC fades to RONIC = WACC over years 6–10: **v2.1 base case** | **20.5%** | **22.1%** | **19.0%** |

What the bridge shows:

1. Most of v1's apparent precision was an artefact of the depressed FCF base. Once SBC is treated as a cost,
   Alphabet has no positive FCF from which to solve, and Meta's figure jumps to 65%. An FCF-growth framing
   is not a usable measure during a capex cycle.
2. Normalising reinvestment (step 4) is the decisive change. Reinvestment then scales with growth at the
   firm's own recent sales-to-capital ratio, instead of extrapolating this year's capex as if permanent.
3. Step 5 raises the required growth: a terminal value that must fund its own reinvestment, with no excess
   returns, is worth less.
4. Step 6 lowers it: the ERP implied in October 2026 is 0.5 pt below the end-2025 value.
5. Step 7 raises it: the fade makes the decline in returns gradual and consistent, and requires more
   reinvestment in years 6–10.

## 4. Sensitivities (implied revenue growth, years 1–5)

| | GOOGL | MSFT | META |
|---|---:|---:|---:|
| Base (v2.1) | 20.5% | 22.1% | 19.0% |
| WACC −1 pt / +1 pt (g_T 3.0%) | 16.8% / 24.3% | 18.4% / 25.8% | 15.7% / 22.5% |
| g_T 2.5% / 4.0% (base WACC) | 20.8% / 20.2% | 22.3% / 21.8% | 19.3% / 18.8% |
| Margin −5 pts / +5 pts | 25.7% / 16.4% | 25.3% / 19.3% | 24.4% / 14.9% |
| Sales-to-capital ×0.5 (capex intensity doubles) / ×2 | 30.2% / 17.5% | 29.3% / 19.4% | 28.5% / 16.1% |
| ERP at end-2025 annual value (4.23%) | 22.7% | 24.1% | 21.5% |
| 21% statutory tax | 21.6% | 22.5% | 23.2% |
| Raw beta instead of Blume | 21.6% | 22.5% | 21.7% |
| ROIC scenario A: abrupt drop at year 11 (Sprint 2.1) | 19.1% | 20.6% | 17.7% |
| ROIC scenario C: fade to half the spread (RONIC 17.3% / 19.1% / 18.9%) | 15.8% | 16.8% | 15.1% |
| ROIC scenario D: forecast ROIC forever (25.6% / 29.6% / 27.8%) | 14.3% | 15.5% | 13.8% |
| RONIC 20% (fade to 20%, held in perpetuity) | 15.2% | 16.6% | 14.9% |

The ranges are wide:
- a ±1 pt WACC error moves implied growth by about ±3.5–4 pts;
- capital intensity moves it by up to +10 pts;
- persistent excess returns (scenarios C and D) lower it by 5–7 pts.

The implied growth is a statement about the price under stated assumptions, not a precise market forecast.

## 5. Independent reproduction

`audit/independent_check.py` shares no calculation function with the engine. It reads:

- the raw SEC companyfacts JSON;
- the raw Yahoo price JSON (including dividends);
- the raw Treasury CSV, FRED CSV and Damodaran XLS.

It then recomputes TTM figures (YTD + FY − prior YTD), beta (closed-form OLS), WACC and the DCF
(explicit arrays, secant solver). **Result: every input and the implied growth match to machine precision
for all three companies.** A first run differed for Alphabet's revenue: the audit script itself had picked
a revenue tag Alphabet stopped using in 2025, and that was fixed in the script, not the engine.

The balance-sheet inputs were also checked against figures read directly from the 10-Qs by a separate
process with no access to this repository:

- cash, debt and leases are identical;
- share counts differ only as described above.

Both re-implementations (`audit/independent_check.py` and the engine) were written by the same author.
The document-level check is the one that is fully independent.

## 6. Limitations (not resolved)

- ERP vintages: 24 vintages since Nov 2021 show no revision of a published monthly value.
  - Before Nov 2021, publication dates are assumed (month start + 14 days).
  - Annual values for 2022 and earlier cannot be checked against their original vintage.
- The fade length (years 6–10) and its linear shape are assumptions; scenarios A–D bound the effect.
- Beta from two years of weekly data has a standard error of 0.17–0.21, so the WACC carries about ±1 pt of
  estimation error.
- A single WACC for a decade, no explicit working capital, and operating margin held at its TTM level
  (sensitivity shown).
- Historical v2 series: computed for 36 MSFT and 36 META releases, but only 11 GOOGL releases. Before FY2021
  Alphabet tagged depreciation with a company-specific XBRL element that the SEC's companyfacts omits, so
  sales-to-capital is not computable. These releases are reported as "not computed", not approximated.
