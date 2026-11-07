# Expectations coverage report (Sprint 2)

Knowledge time 2026-11-07T15:47:41Z. Universe: every earnings release (8-K Item 2.02) of MSFT, GOOGL and META from 2016
to Nov 2026: 133 releases.

**Before Sprint 2:** verified dated consensus existed for 6 releases.
**Now:** a dated pre-release consensus EPS exists for **116 releases (87%)**, and 73 of those were captured
within 30 days of the release. Nothing has been filled in where no source exists.

| Expectation class | GOOGL (n=45) | META (n=44) | MSFT (n=44) | All |
|---|---:|---:|---:|---:|
| Consensus EPS (A/B, before release, ≤150 days old) | 40 (89%) | 39 (89%) | 37 (84%) | 116/133 (87%) |
| …of which captured ≤30 days before release | 26 (58%) | 19 (43%) | 28 (64%) | 73/133 (55%) |
| …with revision path (≥2 dated captures) | 31 (69%) | 31 (70%) | 26 (59%) | 88/133 (66%) |
| Consensus GAAP revenue (A/B) | 0 (0%) | 12 (27%) | 19 (43%) | 31/133 (23%) |
| Consensus revenue ex-TAC (A/B; not comparable to GAAP) | 14 (31%) | 0 (0%) | 0 (0%) | 14/133 (11%) |
| Street-basis actual (C, vendor) | 15 (33%) | 14 (32%) | 17 (39%) | 46/133 (35%) |
| Numeric management guidance (A/B) | 0 (0%) | 19 (43%) | 17 (39%) | 36/133 (27%) |
| Explicit 'no revenue/earnings guidance' (A) | 43 (96%) | 0 (0%) | 0 (0%) | 43/133 (32%) |
| Options-implied move (B, model) | 40 (89%) | 0 (0%) | 0 (0%) | 40/133 (30%) |
| Price-implied FCF growth (B, model) | 37 (82%) | 36 (82%) | 32 (73%) | 105/133 (79%) |
| Narrative items (C) | 2 (4%) | 2 (5%) | 2 (5%) | 6/133 (5%) |
| Measured reaction | 43 (96%) | 42 (95%) | 42 (95%) | 127/133 (95%) |

Dated consensus EPS by period: 2016-2019: 41/48; 2020-2022: 34/36; 2023-2026: 41/49

## Reading the table

- **Consensus EPS** comes from two sources. Most is FactSet consensus shown on MarketWatch pages
  captured by the Internet Archive before the release. The rest comes from dated Zacks and other
  previews, found by a research sub-agent and re-verified by the pipeline (quote present, published and
  last modified before the release). "≤150 days old" includes stale captures: their age is stored per row
  and shown in `expectations/events.md`.
- **Consensus revenue** is thinner (2022 onward, from previews). Alphabet's Zacks revenue is net of TAC
  and is never compared with GAAP revenue.
- **Street-basis actuals** (vendor's EPS on the consensus basis, grade C) exist only where an archived page
  after the release lists them (2020 onward). Before 2020, surprise can only be computed against GAAP EPS.
  This is unreliable where companies or analysts excluded items: Facebook's non-GAAP EPS until 2018,
  Alphabet's EU fines, Microsoft's adjusted FY2016–18 figures. These cases are flagged by the
  `label_check` field.
- **Guidance:**
  - Meta's outlook appears in 25 of 44 releases (it began publishing it in the release in 2019–20).
  - Microsoft is covered from the FY2022 Q3 call onward. Earlier transcripts are no longer online: gap.
  - Alphabet gives no revenue or earnings guidance (43 explicit records). It gave capex guidance in 3
    releases. The July 2025 capex update was given only on the call: gap.
- **Market-implied:** options-implied moves exist for Alphabet only (CBOE VXGOG); no free equivalent exists
  for MSFT or META. Price-implied growth is a model and is labelled as such.

## Descriptive results (no model fitted, no threshold searched)

> Superseded by [CONSENSUS_EPS_AUDIT.md](CONSENSUS_EPS_AUDIT.md) (Sprint 2.1). The EPS-surprise row below
> used 43 releases (2020+ only). The comparable sample is now 86: primary ρ = +0.33, but ρ = +0.24
> (p = 0.056) with fresh consensus only.

| Observation | n | Value |
|---|---:|---|
| Street EPS surprise vs market-model abnormal return on d0: Spearman correlation | 43 | +0.42 |
| …sign agreement (binomial p, two-sided) | 43 | 65% (p = 0.066): suggestive, not significant |
| Share of releases beating street consensus | 43 | 77% |
| Alphabet: median options-implied move vs median realised move | 40 | 5.3% vs 3.8% |
| …share of releases where the realised move exceeded the implied move | 40 | 47.5% |
| Meta revenue vs its own guidance range: below / within / above | 19 | 0 / 10 / 9 |
| Price-driven change in implied growth across d0, same sign as the abnormal return | 105 | 95%. Mechanical: it re-expresses the price move, it is not evidence |

The street-surprise sample covers 2020–2026 only (43 releases), and three stocks share common factors.
None of these figures supports a trading rule.
