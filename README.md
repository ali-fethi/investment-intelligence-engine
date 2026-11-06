# Investment Intelligence Engine — Sprint 1: Data Foundation

An auditable, reproducible, point-in-time public-market data foundation for **GOOGL, MSFT, META**.
Every number keeps its source bytes (sha256), the period it describes, when it became public, and
when it was retrieved. Snapshots reconstruct what was knowable at a cutoff and can be re-verified
bit-for-bit.

* [Architecture](docs/ARCHITECTURE.md): layers, the two-clock time model, selection rules
* [Schema](docs/SCHEMA.md): tables and audit columns (DDL: `src/iie/schema.sql`)
* [Data sources](docs/DATA_SOURCES.md): licensing, point-in-time reliability, documented gaps
* [Sample output](docs/sample_output/): three snapshots, QA summary, verification, search

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

Rebuild the whole database offline from stored evidence (keeps original retrieval timestamps):

```bash
mv data/iie.sqlite data/old.sqlite && .venv/bin/iie rebuild --from-db data/old.sqlite
```

## Tests

```bash
.venv/bin/pytest                                   # 29 offline unit + integration tests (~2 s)
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
src/iie/            config, db + schema.sql, evidence (fetch/store/replay), pit, derive, snapshot, qa, search, cli
src/iie/ingest/     sec, documents, earnings_release, prices, macro, events
tests/              conftest (synthetic sources), test_units, test_pipeline, test_network
docs/               ARCHITECTURE, SCHEMA, DATA_SOURCES, sample_output/
data/               (git-ignored) iie.sqlite, evidence/ (content-addressed gzip), snapshots/
```

## Licensing caveat

SEC, Treasury and FRED/ALFRED data are public (FRED series may carry third-party copyright). Prices
come from Yahoo's unofficial endpoint, which carries no redistribution licence: keep it to personal
research and swap in a licensed vendor before sharing data or outputs commercially.
