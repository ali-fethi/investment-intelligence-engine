# Event Reaction Engine (prototype)

Module: `src/iie/reaction.py` (measurement), `src/iie/expectations.py` (curated, verified expectations),
`src/iie/ingest/macro_events.py` (FOMC + data-release calendar), `src/iie/casestudy.py` (reports).

```
iie ingest benchmarks context macro_events crosscheck expectations
iie case <accession> [...] --knowledge-time T     # one report per earnings release
iie reactions --knowledge-time T                  # all earnings releases since 2016, observable patterns
```

## What it records

| Layer | Source | Timestamp basis |
|---|---|---|
| Company events | EDGAR 8-K/10-Q/10-K (universe + 12 context issuers) | EDGAR acceptance (verified against the filing header) |
| FOMC | federalreserve.gov statement pages | "For release at …" read from each page |
| CPI, payrolls, GDP, PCE | ALFRED vintage dates | date verified; 08:30 ET assumed (agency convention) |
| Corporate actions | price vendor | ex-date (announcement date unknown) |
| Expectations | `registry/expectations.json` → fetched, quote-verified, graded | see grades below |

## What it measures

Windows relative to d0, the first session that could react:

| Window | Span | Notes |
|---|---|---|
| pre-event | 5 days before d0 | |
| overnight gap | close(d0−1) → open(d0) | after-hours + pre-market combined |
| regular session | open(d0) → close(d0) | |
| d0 | close-to-close | |
| d0…d+1 | | |
| d0…d+4 | | |
| post-event | d+1…d+19 | |

Returns:

- Total returns with dividends added back on the ex-date, computed from the vendor's split-adjusted
  series, so they are consistent across splits.
- Benchmarks: SPY, QQQ, the GICS sector ETF (XLK / XLC), and ^GSPC and ^IXIC for close windows only
  (index "opens" are not tradable prints).
- Market-adjusted: excess over each benchmark.
- Market model vs SPY: estimated over d−260…d−11, with at least 120 observations. Reports CAR and the
  standardised CAR (SCAR = CAR / σ_ε√n).
- Partial-day windows are beta-adjusted only, with no z-score.

## What it flags

Overlaps in the d0 reaction period and in d+1…d+4:

- other universe or context earnings;
- the subject's own companion filings;
- ex-dividend dates;
- macro releases.

Attribution uncertainty:

| Level | Condition |
|---|---|
| high | tier-1 macro (FOMC, CPI, payrolls) in the d0 period, or another same-company 8-K |
| medium | tier-2 macro (GDP, PCE), other large-cap earnings, or an ex-dividend date |
| low | none found (untracked news may still exist) |

Each result separates **observable** statements (measured) from **hypotheses and caveats**. Case
reports add dated documented explanations and labelled analyst inference from `registry/case_notes.json`.

## Expectation provenance grades

| Grade | Meaning | Used point-in-time? |
|---|---|---|
| A | Issuer document on EDGAR, accepted before the event (e.g. Meta's CFO outlook) | yes |
| B | Dated source published before the event and not modified after it (Microsoft call transcript by call date; dated Zacks previews) | yes |
| C | Documented only after the event, including pages first published before and rewritten after (CNBC "vs expected") | no: descriptive only |
| D | Quote not found in the stored source, or undated | never |

How a claim is verified:

- Every claim is re-fetched into the evidence store.
- Its verbatim quote is checked against the rendered text (`quote_verified=1`), or failing that
  against the raw page source (`=2`), e.g. article bodies shipped as script data.
- Page metadata overrides a wrong registry date.

Other claim types live in the same table but are never treated as expectations:

| Type | Meaning |
|---|---|
| `reported_kpi` | Actuals quoted from the 8-K |
| `guidance_issued` | Guidance given with the event itself |
| `documented_reaction` | Moves reported by the press |

## Deliberately not done

- No strategy, signal, or optimisation.
- The cross-event summary (127 earnings events, 2016–2026) reports descriptive statistics with sample
  sizes and multiple-testing caveats, and makes no predictive claim.

## Known limitations

| Limitation | Consequence |
|---|---|
| No intraday data | After-hours vs pre-market, and pre- vs post-FOMC within a session, cannot be separated. |
| Sector ETFs contain the subject | Sector-adjusted returns are damped. Index weights are not tracked. |
| Macro calendar coverage | FOMC minutes, jobless claims, ISM and Treasury auctions are not tracked. ALFRED vintage dates include revision-only dates. |
| Consensus coverage | Defensible (A/B) consensus exists for 6 events only, from Zacks previews. No LSEG/FactSet/Visible Alpha data with as-of dates. |
