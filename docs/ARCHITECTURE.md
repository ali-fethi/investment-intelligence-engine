# Architecture — Sprint 1 data foundation

## Purpose

Reconstruct, for any cutoff, what the public market could know about GOOGL, MSFT and META. Every number
must trace back to the exact bytes it came from. Later modules (research, backtesting, committees,
paper trading) will consume this layer. None of them is built here.

## Layers

```
 sources ──► evidence store ──► parsers ──► evidence tables ──► PIT layer ──► snapshots
 (HTTP)      (sha256, gzip,     (pure:      (append-only,       (two clocks)  (immutable JSON + rows,
              one row per       bytes in,    timestamps on                     content hash, verify)
              retrieval)        rows out)    every row)                 ▲
                                                 │                      │
                                                 └──► QA checks ────────┘ (qa_result, data_gap)
```

| Module | Role |
|---|---|
| `evidence.py` | HTTP fetch with per-host rate limits. Each response is stored once by sha256 (gzipped) and gets one `source_document` row: URL, status, retrieval time and licence note. `ReplayTransport` re-serves stored bytes so the database can be rebuilt offline. |
| `ingest/sec.py` | EDGAR submissions (filing index + acceptance timestamps) and XBRL `companyfacts`. Every non-dimensional fact is kept, one row per (fact, filing). |
| `ingest/documents.py` | 10-K/10-Q primary documents and 8-K Item 2.02 exhibits. Also stores each filing's index-headers page, so the acceptance time is confirmed by a second source. The text is extracted, chunked with character offsets, and indexed in SQLite FTS5. |
| `ingest/earnings_release.py` | Reads the GAAP income statement from earnings-release exhibits. This covers the window between the release and the 10-Q/10-K, when the quarter is public but not yet in XBRL. |
| `ingest/prices.py` | Daily bars and splits/dividends, stored exactly as the vendor delivers them, with the vendor's adjustment basis recorded. |
| `ingest/macro.py` | Treasury par curve, FRED daily rates, ALFRED vintages of revised series. Bitemporal insert-if-new. |
| `ingest/events.py` | Event calendar from EDGAR filings (earnings releases, periodic reports, 8-K items) and corporate-action ex-dates. |
| `pit.py` | The only read path for snapshots. It applies `available_at <= cutoff` and `retrieved_at <= knowledge_time` everywhere. |
| `derive.py` | Pure derivations (quarters from year-to-date values, TTM, as-traded price, margins, FCF). Each output carries its methodology and input ids. |
| `snapshot.py` | Builds a snapshot, audits it for look-ahead, hashes it, and persists the JSON, Markdown, `snapshot_item` lineage and `derived_metric` rows. It can also rebuild a snapshot and compare hashes. |
| `qa.py` | Provenance, timing, duplicate, restatement, completeness, definition and reconciliation checks. |
| `search.py` | BM25 evidence retrieval with citations (URL, accession, character range, sha256, public time) and an optional as-of filter. |

## Time model (the core design decision)

Every row carries three distinct times:

* **period** (`period_start`/`period_end`, `obs_date`, `trade_date`): what the number describes.
* **available_at**: when the market could first know it. This is conservative by construction, i.e.
  never earlier than the truth.
  * SEC facts use the EDGAR acceptance timestamp of the carrying filing, cross-checked against the
    filing header (97/97 match).
  * Earnings-release figures use the 8-K acceptance time. This is an upper bound: the issuer's website
    may have published a few minutes earlier.
  * Prices use 16:00 ET on the trade date.
  * FRED daily rates use a lag of one business day (two for DFF), verified against exact ALFRED vintages.
  * ALFRED values use the vintage date.
  * Treasury curve points use the end of the curve date (an unverified assumption).
* **retrieved_at**: when this system fetched it (system time).

A snapshot is the pair (cutoff, knowledge_time). Only rows with `available_at <= cutoff` and
`retrieved_at <= knowledge_time` are visible. Later ingests therefore cannot change an earlier
snapshot. Tests check this, and `iie verify` rebuilds a snapshot and compares content hashes.

## Evidence, derivation and judgment are separate

* Evidence lives in `source_document`, `filing`, `observation`, `price_bar`, `corporate_action`,
  `macro_observation` and `event`. These tables are append-only: SQLite triggers reject UPDATE and
  DELETE. A restatement or a vendor rewrite becomes a new row, never an overwrite.
* Derived metrics live in `derived_metric`. Each row is tied to the snapshot (cutoff) it was computed
  for, with methodology, input row ids, and `available_at = max(inputs)`.
* `analyst_judgment` is reserved and empty. Later agents will write there and cite evidence ids.

## Selection rules at a cutoff (`pit.load_facts`)

1. Visibility filter (both clocks).
2. Structured XBRL beats the earnings-release table as soon as XBRL is visible, including a quarter
   *derived* from XBRL year-to-date values. The release row is then shown under `supersedes`.
3. Concept priority per canonical metric (`config.METRICS`). Lower-priority concepts that disagree are
   reported as definition conflicts. Example: Microsoft FY2016–17 `SalesRevenueNet` vs the ASC 606
   concept.
4. The latest *available* report of a period wins, which handles restatements. The first-reported
   value and the number of reports stay in `revision_history_at_cutoff`.
5. Non-additive metrics (EPS, share counts) are never derived. A missing Q4 EPS is a documented gap.

## Prices

The vendor's `close` is split-adjusted as of the retrieval date, so its history is not point-in-time.
The as-traded close is `vendor_close × Π(split ratios with trade_date < ex_date ≤ retrieval_date)`.
Post-cutoff splits are used only to undo the vendor's own retroactive adjustment. Returns at a cutoff
are adjusted only for splits known by that cutoff. Example: GOOGL at 2022-06-30 is $2,179.26 as traded
(vendor $108.96 × 20).

## Reproducibility

* `iie rebuild --from-db OLD` replays every stored response, with its original `retrieved_at`, through
  the current parsers into a fresh database, with no network access. This was used in this sprint
  after fixing the DFF lag assumption, because append-only tables cannot be corrected in place.
* A snapshot hash covers the full canonical JSON content (not creation metadata). `verify` checks: the
  file hash, the rebuild hash, the code version, the evidence bytes (sha256) and the look-ahead audit.

## Milestone 2 additions

`reaction.py` (Event Reaction Engine), `expectations.py` (claim registry with quote verification and
provenance grades), `casestudy.py` (reports), `ingest/macro_events.py`, `ingest/crosscheck.py`.

- They read prices only through `PriceSeries`, which applies the same two clocks as `pit.py`. It also
  excludes bars retrieved before their session closed.
- Estimation windows end 10 sessions before the event.
- Expectations enter a computation only if graded A/B and available before the event's public timestamp.

See [EVENT_ENGINE.md](EVENT_ENGINE.md).

## Sprint 2 additions

`src/iie/expect/` builds `expectation_obs`, a point-in-time store of expectations. Every value has a
dated source: an EDGAR acceptance, an Internet Archive capture, a page published and not modified before
the event, or our own retrieval for forward snapshots. Readers see only grade A/B rows available before an
event's public time. `expect/link.py` joins expectations, outcomes and the reaction engine per release. See
[EXPECTATIONS_ENGINE.md](EXPECTATIONS_ENGINE.md).

## Deliberate non-goals (Sprint 1)

No strategy, no sentiment, no recommendations, no UI. Values are reported as the filer reported them;
no cross-company harmonisation of definitions beyond the documented concept mapping.
