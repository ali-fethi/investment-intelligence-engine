# Database schema

The authoritative DDL is [`src/iie/schema.sql`](../src/iie/schema.sql) (SQLite, created by `iie init`).
This page explains how the tables relate and which columns carry the audit trail.

```
company ─┬─< filing ─────────────< (accession_no) >─ observation
         │      │                                       │
         │      └─ source_document (submissions json)   ├─ source_document (companyfacts json / EX-99.1 html)
         ├─< price_bar ──────── source_document (vendor json)
         ├─< corporate_action ─ source_document
         ├─< event ──────────── source_document
         └─< derived_metric >── snapshot ─< snapshot_item (table_name, row_id) ──► any evidence row
macro_observation ── source_document (csv)
document_chunk ── source_document; chunk_fts (FTS5 index over chunk text)
qa_result, data_gap, ingest_run  (process metadata)
analyst_judgment                 (reserved; empty in Sprint 1)
```

## Evidence tables (append-only: UPDATE/DELETE raise `append-only`)

| Table | Grain | Audit columns |
|---|---|---|
| `source_document` | one HTTP retrieval | `url`, `sha256` (raw bytes), `storage_path`, `http_status`, `retrieved_at`, `published_at` + `published_at_basis`, `license_note`, `run_id` |
| `filing` | one EDGAR accession | `acceptance_datetime` (UTC), `filing_date`, `report_date`, `items`, `source_doc_id` |
| `observation` | one reported fact in one filing (XBRL) or one release-table value | `taxonomy`/`concept`, `accounting_definition`, `unit`, `period_start`/`period_end`, `fiscal_year`/`fiscal_period` (recomputed from dates), `filing_fiscal_*` (as tagged by the filer), `accession_no`, `source_doc_id`, `source_url`, `published_at`, `available_at`, `availability_basis`, `availability_verified`, `retrieved_at`, `reliability`, `extraction_method`, `natural_key` |
| `price_bar` | one vendor bar per retrieval | `adjustment_basis`, `available_at` (16:00 ET), `availability_basis`, `source_doc_id`, `retrieved_at` |
| `corporate_action` | split / dividend | `ex_date`, `ratio`/`amount`, `amount_basis`, `announced_at` (NULL when unknowable), `available_at`, `availability_basis` |
| `macro_observation` | one value as first seen in a vintage/retrieval | `obs_date`, `vintage_date`, `available_at`, `availability_basis`, `availability_verified`, `source_url` |
| `event` | one dated event | `event_date` (what happened) vs `available_at` (when public), `accession_no`, `details_json` |

Uniqueness: `observation(natural_key, value)`, `price_bar(natural_key, close, volume)` and
`corporate_action(natural_key, ratio, amount)` make re-ingestion idempotent. A *different* value for
the same natural key is stored as a new row: a restatement, a vendor rewrite, or a parser change.
QA surfaces each of these.

## Reliability vocabulary (`observation.reliability`)

* `primary_regulatory_structured`: SEC XBRL fact.
* `primary_issuer_unstructured`: number parsed from an issuer document filed on EDGAR (earnings release).
* `secondary_vendor_unlicensed`: price vendor (on snapshot items).

## Derived and judgment tables

| Table | Notes |
|---|---|
| `derived_metric` | `snapshot_id`, `as_of_cutoff`, `methodology`, `inputs_json` (row ids + values + availability), `available_at = max(inputs)`, `code_version`. Never used as an input to evidence. |
| `analyst_judgment` | Reserved for later sprints; every statement must cite evidence ids. |

## Snapshot tables (immutable)

`snapshot(cutoff, knowledge_time, content_sha256, path, code_version)` and `snapshot_item` (every
evidence row the snapshot cites). The JSON file under `data/snapshots/` holds the full content.
Its hash is recomputed by `iie verify`.

## v2 additions (Milestone 2)

- `company.kind` (issuer | etf | index) and `company.role` (universe | context | benchmark); `cik` is
  nullable for ETFs and indices. Migrated in place: ids and snapshots are unchanged.
- `expectation`: curated claims (guidance, consensus, reported KPIs, guidance issued with the event,
  documented reactions). Each carries `source_doc_id`, `verbatim_quote`, `quote_verified` (0/1/2),
  `provenance_grade` (A–D) and `available_at`, which is NULL unless the grade is A/B. Append-only;
  readers take the latest row per `natural_key`.
- `event_reaction`: engine results, with result JSON, data-through date, knowledge time and code
  version. Append-only.
- `data_correction`: append-only record of errors found in stored evidence (table, row filter, field,
  wrong → correct value, reason, how it was established).
- `event` rows with `company_id IS NULL` are macro events (FOMC statements, CPI/payrolls/GDP/PCE
  release dates).

## Canonical metric mapping

`metric_definition` + `metric_concept_map` (from `config.METRICS`): e.g. `revenue` ←
`RevenueFromContractWithCustomerExcludingAssessedTax` (priority 0), `Revenues` (1), `SalesRevenueNet`
(2). No harmonisation is applied beyond choosing a concept, and conflicts are reported.
