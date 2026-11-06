# QA and evidence-provenance report (v0.2.0, 6 Nov 2026)

## Automated tests: 44 (41 offline, 3 live)

| Area | Tests | What they attack |
|---|---|---|
| Look-ahead & timestamps | `test_pipeline` (release window, restatements, macro vintages, knowledge-time freeze); `test_reaction::test_estimation_window_excludes_event_and_future`, `::test_later_retrieval_does_not_change_result_at_earlier_knowledge_time`, `::test_incomplete_bar_is_ignored`; network checks of EDGAR UTC conventions | Corrupting post-event and pre-event-gap prices must not change the market model; later vendor rewrites must not change results at an earlier knowledge time; mid-session bars are ignored. |
| Price adjustments & corporate actions | `test_units::test_as_traded_close…`, `test_reaction::test_dividend_and_split_handling`, `test_pipeline::test_prices_are_as_traded_at_cutoff`; live check that Yahoo is split-adjusted | Split day ≠ −95%; dividend added on the ex-date open; a split not yet known at the cutoff is not applied. |
| Benchmarks & returns | `test_reaction::test_windows_and_abnormal_returns_match_hand_calculation` | Hand-computed gap, session and close-to-close returns; recovers the known β = 1.5 and the injected +5% abnormal return; index opens never used. |
| Simultaneous events | `test_reaction::test_overlap_and_attribution` | Peer earnings the same evening → medium; FOMC at 14:00 on d0 → high, with the decision parsed from the stored page; a next-day event lands in d+1…d+4, not d0. |
| Missing / unreliable expectations | `test_reaction::test_expectation_grading_rules`, `::test_registry_verification_and_point_in_time_use`, `::test_page_rewritten_after_event_is_not_point_in_time`, `::test_event_without_expectations_reports_absence` | A fabricated quote is graded D and never usable; a wrong registry date is overridden by page metadata; a page published before but modified after the event is graded C; absence is reported, not filled. |
| Reproducibility | snapshot verify/rebuild tests; reaction content hashes; migration test | Sprint 1 snapshots rebuild bit-for-bit under v0.2.0; case results re-hash identically at the same knowledge time; v1→v2 schema migration keeps ids and data. |

## QA run on the full database (0 fail)

| Check | Result |
|---|---|
| EDGAR acceptance vs filing header | 97/97 identical |
| Earnings release vs later XBRL | 177 exact, 3 within $1m rounding, 0 mismatched (Sprint 1, unchanged) |
| Price cross-source (Yahoo vs Nasdaq.com, 2,108–2,513 days per instrument) | 6 instruments with 0 mismatches above 5 bp; SPY 1 mismatch, classified *Nasdaq stale repeat* (warn) |
| ^GSPC / ^IXIC vs FRED | 2 and 9 days above 1 bp out of ~2,500 (warn, documented; the largest is ^IXIC late April 2019, ~15 bp for several days) |
| FRED publication-lag assumption vs exact ALFRED vintages | 24/24 pass |
| FOMC statement times | 93 statements, all with a stated release time (86 at 14:00 ET; 7 unscheduled or non-rate releases) |
| Incomplete (mid-session) bars | 6 stored as evidence, excluded by every reader |
| 8-K event date vs acceptance | 46/701 differ (combined 8-Ks); readers use acceptance; correction recorded |
| Expectation provenance | 0 grade-C/D claims marked usable point-in-time |
| Snapshot look-ahead | 3/3 pass |

## Provenance of curated claims (registry, 67 claims)

| Type | A | B | C | Quote check |
|---|---:|---:|---:|---|
| Management guidance (pre-event) | 2 | 6 | – | 8/8 in rendered text |
| No-guidance policy | 2 | – | – | 2/2 |
| Sell-side consensus | – | 14 | 18 | 12 rendered, 2 raw-source only (Yahoo script data), 18 rendered |
| Reported KPIs (8-K) | 5 | – | – | 5/5 |
| Guidance issued with the event | – | – | 5 | 5/5 (post-event by nature) |
| Documented reactions | – | – | 15 | 15/15 |

How each claim was verified:

- A research sub-agent proposed every claim from public sources.
- The pipeline then re-downloaded every source independently and re-checked each quote against the
  stored bytes.
- The provenance grade is computed from timestamps (EDGAR acceptance, page metadata, and the
  modification time when present), never assigned by hand.

## Corrections recorded (`data_correction`, append-only)

1. `price_bar.adjustment_basis` for Nasdaq.com rows: the "as-traded" label is wrong; the history is
   split-adjusted.
2. `event.event_date` for combined 8-Ks: the report_date is replaced by the acceptance date for display
   and keys.

Code fixes made in the same pass:

- FOMC decision parser.
- Rejection of mid-session bars.
- DFF publication lag (Sprint 1).
- Grading of pages rewritten after the event.
- Unknown fiscal-year-end fallback.

## What passing tests do *not* establish

- That reported figures are economically correct. They are as filed; restatements are tracked.
- That press-quoted consensus figures equal the provider's true pre-event consensus. Grade C is never
  used point-in-time.
- That a measured abnormal return was caused by the release. Attribution uncertainty is reported, and
  causation is stated only as a hypothesis.
- Licence compliance for vendor data beyond personal research (Yahoo, Nasdaq.com, press pages).
