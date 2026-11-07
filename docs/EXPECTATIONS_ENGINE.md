# Expectations Engine (Sprint 2)

Code: `src/iie/expect/` (`archive`, `parsers`, `consensus`, `guidance`, `implied`, `link`, `report`, `store`).
Table: `expectation_obs` (append-only; schema v3).

```
iie expect consensus guidance            # archived consensus pages; issuer guidance
iie ingest expectations                  # re-verify curated claims in registry/expectations.json
iie expect registry implied              # map claims into classes; options-implied moves; price-implied growth
iie expect-report [--cutoff T]           # coverage, linked event table, hypotheses, audit -> docs/expectations/
```

## Five classes, kept apart

| Class | What it is | Sources in this build | Typical grade |
|---|---|---|---|
| `reported_result` | What the company reported | GAAP figures from the 8-K and XBRL (`observation`, A); KPIs quoted from 8-Ks (A); the vendor's "street" actual (C) | A / C |
| `management_guidance` | What management said it expects | Meta CFO outlook in 8-K exhibits (A); Microsoft call transcripts (B); Alphabet's explicit absence of guidance, checked release by release (A) | A / B |
| `analyst_consensus` | What sell-side analysts expected, as published at a date | MarketWatch/FactSet pages archived by the Internet Archive before the release (B); dated Zacks/GuruFocus/StockStory/Barchart previews (B); post-release "vs expected" pages (C) | B / C |
| `market_implied` | What prices imply (models, labelled as such) | Options-implied event move from CBOE VXGOG (Alphabet only); price-implied 10-year FCF growth (reverse DCF), before and after each release | B (model) |
| `narrative` | How the press characterised it | Dated press quotes ("light guidance", "shares slid 6% in extended trading") | C |

Narrative is recorded as quoted text with its source and date. It is not scored and never enters a
calculation.

## Point-in-time rules

- **Usability.** An observation is usable for an event only if its grade is A or B and its
  `available_at` is strictly before the release's EDGAR acceptance time. `store.put` forces
  `available_at = NULL` for grades C and D.
- **Archive timestamps.** An Internet Archive capture is independent, third-party evidence that the page
  content was public at the capture time. Captures after the release are used only for grade-C
  history: the vendor's consensus "at report" and the street actual.
- **Old MarketWatch layout (2016–2019).** These pages carry no quarter label. A value is assigned to a
  quarter only when the previous quarter had already been reported at capture time. The assignment is
  then checked: the page's "Last Quarter's Earnings" is compared with GAAP EPS from EDGAR. Mismatches
  are kept but flagged, because the street basis can differ from GAAP.
- **Splits.** A consensus captured before a stock split and used for a release after it is rebased by
  the split ratio (GOOGL 20:1, July 2022). The adjustment is recorded on the row.
- **Revisions.**
  - Consensus: every dated capture is kept, and the linked table reports the revision between the
    earliest and latest usable capture.
  - Guidance: each release's outlook is a new observation of the same target. Meta's own wording ("updated
    from our prior outlook of $X–Y") is cross-checked against the range Meta actually published
    earlier.
- **Same basis.** Surprise is computed street-vs-street where the vendor's street actual exists, and also
  GAAP-vs-consensus. The gap between the two is reported, since one-off items make GAAP and street EPS
  differ. Alphabet's Zacks "revenue" is net of TAC and is stored as `revenue_ex_tac`, never compared with
  GAAP revenue.

## Models (market-implied)

- **Event move (Alphabet):**
  σ²_event = (IV_pre² − IV_base²)·T + IV_base²·T/N, with T = 30/365 and N = 21.
  - IV_pre is the last VXGOG close that was public before the release.
  - IV_base is the VXGOG close ~35 days earlier.
  - Expected |move| = √(2/π)·σ_event.
  - Assumptions: constant diffusive volatility, and no other scheduled event in the window.
- **Price-implied growth:** the 10-year FCF growth that equates a two-stage DCF with the as-traded
  market value.
  - Discount rate: 10-year Treasury (point-in-time) plus an ERP of 4%, 5% or 6%.
  - Terminal growth: 3%.
  - Simplifications: TTM FCF = CFO − capex; diluted weighted shares as the share count; net cash ignored.
  - It restates the price as an assumption. It is not a forecast, and nothing is fitted to prices.

## What is deliberately not done

- No forecast is produced. Prices are not used to "correct" any fundamental number.
- No pattern is optimised.
- No recommendation, strategy or committee.
