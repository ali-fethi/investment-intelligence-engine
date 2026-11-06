# Proposed next milestone: Expectations Engine

## Problem

The reaction engine can measure *how much* a stock moved and *what else happened*. It cannot yet say
whether a release surprised the market:

- **Too few defensible expectations.** Point-in-time (grade A/B) expectations exist for only 6 of 127
  events.
- **Guidance coverage is thin.** It is complete for Meta, which publishes its outlook on EDGAR (grade A),
  and partial for Microsoft, from call transcripts (grade B).
- **Consensus coverage is thinner.** It comes only from dated Zacks previews (grade B). The figures the
  press quotes (LSEG, StreetAccount) are documented only after the fact (grade C), and two providers can
  disagree by 3 points on the same metric (Azure, Oct 2024).

## Goal

For every event in the universe, a point-in-time record of what management and the market expected,
with provenance strong enough for backtesting. Then a measured surprise per metric, reported next to
the reaction with no causal claim.

## Scope (4 work packages)

1. **Guidance extraction.**
   - Parse "outlook" sections from 8-K exhibits (Meta, grade A).
   - Parse call transcripts and outlook slides from the Microsoft IR site (grade B). The ~40 historical
     Microsoft calls follow the same structure.
   - Record "no guidance" as an explicit observation for Alphabet.
   - Every value keeps its verbatim quote and character offsets, re-verified automatically.
2. **Consensus with as-of dates.**
   - Option A, free: dated preview articles (Zacks/Yahoo, Nasdaq.com previews). Grade B, coverage
     patchy, single provider.
   - Option B, licensed: a consensus history with as-of snapshots (e.g. LSEG I/B/E/S, FactSet Estimates,
     Visible Alpha, or a lower-cost API such as FMP/Finnhub/Polygon estimates, after checking their as-of
     semantics). Required for a backtest-grade surprise series. **A spending decision.**
3. **Actuals on the same definitions.** Segment and KPI actuals (Azure growth, Cloud revenue, ad revenue)
   from release text and XBRL dimensional facts (the full XBRL instances already stored with each
   filing), mapped to the exact definition the expectation uses. Segment recasts (e.g. Microsoft, Aug
   2024) become definition-change events.
4. **Surprise and QA.**
   - Standardised surprise = (actual − expectation) / dispersion, where dispersion is available.
   - Adversarial tests: post-event rewrites, provider disagreement, definition changes, ex-TAC vs GAAP
     revenue mismatches.
   - Reproducibility hashes.

## Acceptance criteria

- At least 90% of 2020–2026 earnings events for MSFT, GOOGL, META have management guidance (or an
  explicit no-guidance record) at grade A/B.
- Consensus revenue and EPS at grade A/B for at least 80% of the same events, if option B is approved.
  Otherwise the coverage achieved is reported, not padded.
- Zero grade-C/D values used in any point-in-time computation; an adversarial test suite proves it.
- A cross-event table of reaction versus surprise. It stays descriptive: no fitted model until a
  separate evaluation sample is defined.

## Out of scope

Recommendations, trading signals, multi-agent committees.
