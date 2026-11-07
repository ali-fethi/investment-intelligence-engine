# Reverse DCF: audit of v1, methodology of v2, and valuation bridge (Sprint 2.1)

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
- FCFF_t = R_t·m·(1−τ) − (R_t − R_{t−1})/S2C.
- TV = R₁₀(1+g_T)·m·(1−τ)·(1 − g_T/RONIC)/(WACC − g_T).
- EV = Σ FCFF_t/(1+WACC)ᵗ + TV/(1+WACC)¹⁰.
- g is solved such that EV + cash − debt − leases = price × shares.

| Input | Source and timing | GOOGL | MSFT | META |
|---|---|---:|---:|---:|
| Risk-free rate | 10-year Treasury par yield, Treasury CSV, 5 Nov 2026 | 4.84% | 4.84% | 4.84% |
| ERP | Damodaran implied ERP (FCFE) at end-2025, `histimpl.xls` (published January 2026) | 4.23% | 4.23% | 4.23% |
| Beta | OLS, 104 weekly total returns vs SPY (Nov 2024 – Nov 2026); Blume-adjusted | 1.26 → **1.17** (s.e. 0.19) | 1.11 → **1.07** (0.17) | 1.68 → **1.45** (0.21) |
| Cost of equity | r_f + β·ERP | 9.81% | 9.37% | 10.98% |
| Pre-tax cost of debt | Moody's Aaa (FRED AAA), Sept 2026 average (October not yet published at the date) | 6.03% | 6.03% | 6.03% |
| Tax rate | TTM provision / TTM pre-tax income, bounded to [10%, 30%] | 18.1% | 19.7% | 4.6% → **10%** (bounded) |
| Weight of debt | Book debt + finance leases over (that + market cap) | 3.6% | 2.6% | 5.1% |
| **WACC** | | **9.63%** | **9.25%** | **10.70%** |
| Operating margin (TTM GAAP) | XBRL | 33.3% | 47.1% | 32.9% |
| Sales-to-capital | Δrevenue over 3 fiscal years / Σ(capex − depreciation) | 0.94 (FY23–25) | 0.78 (FY24–26) | 0.94 (FY23–25) |
| Terminal growth | min(r_f, 10-year breakeven 2.26% + 1%) | 3.26% | 3.26% | 3.26% |
| EV | market cap + debt + leases − cash and short-term investments | $3,360bn | $4,022bn | $1,558bn |
| **Implied revenue growth, years 1–5** | | **21.2%** | **22.5%** | **20.1%** |
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
| 5 | Terminal g_T anchored, with terminal reinvestment (RONIC = WACC): **v2 base case** | **21.2%** | **22.5%** | **20.1%** |

What the bridge shows:

1. Most of v1's apparent precision was an artefact of the depressed FCF base. Once SBC is treated as a cost,
   Alphabet has no positive FCF from which to solve, and Meta's figure jumps to 65%. An FCF-growth framing
   is not a usable measure during a capex cycle.
2. Normalising reinvestment (step 4) is the decisive change. Reinvestment then scales with growth at the
   firm's own recent sales-to-capital ratio, instead of extrapolating this year's capex as if permanent.
3. Step 5 raises the required growth: a terminal value that must fund its own reinvestment, with no excess
   returns, is worth less.

## 4. Sensitivities (implied revenue growth, years 1–5)

| | GOOGL | MSFT | META |
|---|---:|---:|---:|
| Base | 21.2% | 22.5% | 20.1% |
| WACC −1 pt / +1 pt (g_T 3.0%) | 17.8% / 24.8% | 19.2% / 26.0% | 17.0% / 23.4% |
| g_T 2.5% / 4.0% (base WACC) | 21.6% / 20.8% | 22.9% / 22.2% | 20.5% / 19.7% |
| Margin −5 pts / +5 pts | 26.2% / 17.2% | 25.7% / 19.9% | 25.3% / 16.0% |
| Sales-to-capital ×0.5 (capex intensity doubles) / ×2 | 31.8% / 18.3% | 30.0% / 20.1% | 30.9% / 17.2% |
| RONIC 20% (growth creates value in perpetuity) | 17.5% | 18.7% | 17.4% |
| 21% statutory tax | 22.3% | 23.0% | 24.2% |
| Raw beta instead of Blume | 22.4% | 23.0% | 23.0% |

The ranges are wide. A ±1 pt WACC error moves implied growth by ±3–4 pts, and capital intensity moves it
by up to +10 pts. The implied growth is a statement about the price under stated assumptions, not a
precise market forecast.

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

- Damodaran's historical values come from the current file. If the author has revised past values, that
  cannot be seen.
- Beta from two years of weekly data has a standard error of 0.17–0.21, so the WACC carries about ±1 pt of
  estimation error.
- A single WACC for a decade, no explicit working capital, and operating margin held at its TTM level
  (sensitivity shown).
- Historical v2 series: computed for 36 MSFT and 36 META releases, but only 11 GOOGL releases. Before FY2021
  Alphabet tagged depreciation with a company-specific XBRL element that the SEC's companyfacts omits, so
  sales-to-capital is not computable. These releases are reported as "not computed", not approximated.
