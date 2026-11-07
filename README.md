# Investment Intelligence Engine — data foundation, Event Reaction Engine, Expectations Engine (v0.3.0)

An auditable, reproducible, point-in-time public-market data foundation for **GOOGL, MSFT, META**.
Every number keeps its source bytes (sha256), the period it describes, when it became public, and
when it was retrieved. Snapshots reconstruct what was knowable at a cutoff and can be re-verified
bit-for-bit.

* [Architecture](docs/ARCHITECTURE.md): layers, the two-clock time model, selection rules
* [Schema](docs/SCHEMA.md): tables and audit columns (DDL: `src/iie/schema.sql`)
* [Data sources](docs/DATA_SOURCES.md): licensing, point-in-time reliability, documented gaps
* [Sample output](docs/sample_output/): three snapshots, QA summary, verification, search

**Milestone 2 (v0.2.0): Event Reaction Engine**
* [Event Reaction Engine](docs/EVENT_ENGINE.md): windows, benchmarks, market model, overlaps, expectation grades
* [Corrected Microsoft 30 Jan 2024 analysis](docs/MSFT_2024-01-30_CORRECTED.md) and
  [five validation cases](docs/VALIDATION_CASES.md); full reports and the 127-event summary in [`docs/event_engine/`](docs/event_engine/)
* [QA and provenance report](docs/QA_PROVENANCE_REPORT.md)
* [Next milestone: Expectations Engine](docs/EXPECTATIONS_ENGINE_PROPOSAL.md)
* Curated, machine-verified claims: [`registry/expectations.json`](registry/expectations.json), [`registry/case_notes.json`](registry/case_notes.json)

**Sprint 2 (v0.3.0): Expectations Engine**
* [Expectations Engine](docs/EXPECTATIONS_ENGINE.md): five classes (reported, guidance, consensus, market-implied, narrative), grades, point-in-time rules
* [Coverage report](docs/EXPECTATIONS_COVERAGE.md) · [Sources investigated](docs/EXPECTATIONS_SOURCES.md) ·
  [Checked examples](docs/EXPECTATIONS_EXAMPLES.md) · [Market vs fundamentals hypotheses](docs/MARKET_VS_FUNDAMENTALS.md) ·
  [Audit](docs/EXPECTATIONS_AUDIT.md); generated tables in [`docs/expectations/`](docs/expectations/)

## SEC contact (one-time, local only)

The SEC asks automated tools to identify themselves. Open the file `.env` in this folder (it is never
committed), fill in the two lines `SEC_CONTACT_NAME=` and `SEC_CONTACT_EMAIL=`, and save. Until then a
placeholder is used and every EDGAR command prints a reminder.

## Install

Requires Python ≥ 3.9 (tested on 3.9.6, macOS) and SQLite with FTS5 (bundled with Python).

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
```

The SEC requires a contact in the User-Agent. Set yours before ingesting:

```bash
export SEC_USER_AGENT="YourName research you@example.com"
```

## Run

```bash
.venv/bin/iie init
.venv/bin/iie ingest all --vintage 2022-06-30 2024-01-29 2024-01-30 2026-11-05   # ~10 min, network
.venv/bin/iie qa
.venv/bin/iie snapshot --cutoff 2024-01-30T17:30:00          # naive time = New York; a bare date = 23:59:59 ET
.venv/bin/iie verify --snapshot 1                             # rebuild + hash compare + evidence re-hash
.venv/bin/iie search "capital expenditures data centers" --ticker META --as-of 2024-01-30
.venv/bin/iie status
```

Ingest targets can be run separately: `sec`, `docs`, `releases`, `prices`, `events`, `macro`. Options:
`--tickers`, `--since` (history start, default 2016-01-01), `--docs-since` (full-text documents,
default 2023-01-01), `--vintage-start`, `--vintage` (exact ALFRED vintages, e.g. your snapshot
cutoffs).

Milestone 2 commands:

```bash
.venv/bin/iie ingest benchmarks context macro_events crosscheck expectations
.venv/bin/iie case 0000950170-24-008809 --knowledge-time 2026-11-06T16:00:00Z   # one report per accession
.venv/bin/iie reactions                                                         # all earnings releases since 2016
```

Sprint 2 commands:

```bash
.venv/bin/iie expect consensus guidance          # archived consensus pages (slow: ~8 s per page) + guidance
.venv/bin/iie ingest expectations                # re-verify curated claims (registry/expectations.json)
.venv/bin/iie expect registry implied forward    # classes, market-implied models, today's forward consensus
.venv/bin/iie expect-report --cutoff 2026-11-05  # coverage, linked events, hypotheses, audit
```

Rebuild the whole database offline from stored evidence (keeps original retrieval timestamps):

```bash
mv data/iie.sqlite data/old.sqlite && .venv/bin/iie rebuild --from-db data/old.sqlite
```

## Tests

```bash
.venv/bin/pytest                                   # 58 offline unit, integration and adversarial tests (~2 s)
IIE_NETWORK_TESTS=1 .venv/bin/pytest -m network    # 3 live checks of provider formats and timestamp conventions
```

The offline integration suite runs the real ingest code against synthetic sources. It covers: the
release-only window, restatements, YTD-derived quarters, as-traded prices around a split, macro
vintages, snapshot reproducibility, immutability, look-ahead and QA detection.

## Acceptance test (run on 2026-11-06)

| Cutoff | What it demonstrates | Result |
|---|---|---|
| 2022-06-30 23:59 ET | GOOGL **before** its 20:1 split: as-traded $2,179.26 (vendor $108.96 × 20), EPS $24.62 as originally reported | look-ahead pass (378 items), rebuild hash match |
| 2024-01-30 17:30 ET | Alphabet's Q4 release public (16:01 ET) but its 10-K not (21:43 ET): Q4 comes from the 8-K EX-99.1 table. Microsoft's 10-Q (16:05 ET) is visible. Meta is still on Q3. The Treasury curve and FRED rates stop at 01-29. | look-ahead pass (357 items), rebuild hash match |
| 2026-11-05 23:59 ET | Latest state: FY2026 Q3 (GOOGL, META), FY2027 Q1 (MSFT) | look-ahead pass (433 items), rebuild hash match |

QA over the full database: 0 failures. Highlights:

* 97/97 EDGAR acceptance timestamps match the filing headers.
* 192 earnings-release values: 177 reconciled exactly with later XBRL, 3 within $1m rounding,
  12 awaiting an XBRL counterpart (Q4 EPS, which is never derived), 0 mismatches.
* Balance-sheet identity holds in every filing.
* The GOOGL split matches XBRL.
* FRED lag assumptions hold at 24/24 exact ALFRED vintages.

Warnings are documented gaps (see `docs/sample_output/qa_summary.txt`).

## Layout

```
src/iie/            config, db + schema.sql, evidence (fetch/store/replay), pit, derive, snapshot, qa, search, cli,
                    reaction (event engine), expectations (claim registry), casestudy (reports)
src/iie/expect/     archive, parsers, consensus, guidance, implied, forward, link, report, store
src/iie/ingest/     sec, documents, earnings_release, prices, macro, events, macro_events, crosscheck
registry/           curated claims (expectations.json) and labelled case notes (case_notes.json)
tests/              conftest (synthetic sources), test_units, test_pipeline, test_reaction, test_network
docs/               ARCHITECTURE, SCHEMA, DATA_SOURCES, EVENT_ENGINE, QA_PROVENANCE_REPORT, VALIDATION_CASES,
                    MSFT_2024-01-30_CORRECTED, EXPECTATIONS_ENGINE_PROPOSAL, event_engine/, sample_output/
data/               (git-ignored) iie.sqlite, evidence/ (content-addressed gzip), snapshots/
```

## Licensing caveat

SEC, Treasury and FRED/ALFRED data are public (FRED series may carry third-party copyright). Prices
come from Yahoo's unofficial endpoint, which carries no redistribution licence: keep it to personal
research and swap in a licensed vendor before sharing data or outputs commercially.
