-- Investment Intelligence Engine — Sprint 1 schema (SQLite)
--
-- Conventions
--   * Timestamps are ISO-8601 UTC strings: 'YYYY-MM-DDTHH:MM:SSZ'.  Dates are 'YYYY-MM-DD'.
--   * "period_*" columns describe WHAT the number measures (the economic period).
--   * "available_at" is WHEN the number became publicly available (first-known availability).
--   * "retrieved_at" is WHEN this system fetched it.  Snapshots filter on both
--     available_at <= cutoff (market time) and retrieved_at <= knowledge_time (system time).
--   * Evidence tables (source_document, observation, price_bar, macro_observation, filing,
--     event, corporate_action) are append-only: UPDATE and DELETE are rejected by triggers.
--   * Derived metrics and analyst judgments live in separate tables and never overwrite evidence.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ingest_run (
    run_id        INTEGER PRIMARY KEY,
    component     TEXT NOT NULL,
    params_json   TEXT NOT NULL,
    code_version  TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running',   -- running | ok | failed
    stats_json    TEXT,
    notes         TEXT
);

-- Instruments and issuers.  role: universe (fully ingested) | context (EDGAR filings only, used to
-- detect overlapping events) | benchmark (prices only).  kind: issuer | etf | index.
CREATE TABLE IF NOT EXISTS company (
    company_id       INTEGER PRIMARY KEY,
    ticker           TEXT NOT NULL UNIQUE,
    cik              TEXT UNIQUE,                     -- 10-digit, zero padded; NULL for ETFs/indices
    name             TEXT NOT NULL,
    fiscal_year_end  TEXT NOT NULL,                   -- 'MMDD' as reported by EDGAR ('0000' if n/a)
    exchange         TEXT,
    kind             TEXT NOT NULL DEFAULT 'issuer',
    role             TEXT NOT NULL DEFAULT 'universe'
);

-- Every artifact fetched from the outside world. One row per retrieval (immutable).
-- The bytes live in the content-addressed evidence store at storage_path (gzip of raw bytes);
-- sha256 is computed over the raw (uncompressed) bytes.
CREATE TABLE IF NOT EXISTS source_document (
    doc_id              INTEGER PRIMARY KEY,
    source_system       TEXT NOT NULL,     -- sec_edgar | sec_xbrl_api | yahoo_chart | us_treasury | fred | alfred
    doc_type            TEXT NOT NULL,     -- e.g. companyfacts_json, submissions_json, 10-K, 10-Q, EX-99.1, price_json, csv
    url                 TEXT NOT NULL,
    company_id          INTEGER REFERENCES company(company_id),
    accession_no        TEXT,
    title               TEXT,
    sha256              TEXT NOT NULL,
    bytes               INTEGER NOT NULL,
    content_type        TEXT,
    storage_path        TEXT NOT NULL,
    http_status         INTEGER NOT NULL,
    published_at        TEXT,              -- publisher's own timestamp when verifiable
    published_at_basis  TEXT,              -- how published_at was established
    retrieved_at        TEXT NOT NULL,
    license_note        TEXT,
    run_id              INTEGER REFERENCES ingest_run(run_id)
);
CREATE INDEX IF NOT EXISTS ix_doc_sha ON source_document(sha256);
CREATE INDEX IF NOT EXISTS ix_doc_acc ON source_document(accession_no);

-- EDGAR filing index (from the submissions API).
CREATE TABLE IF NOT EXISTS filing (
    accession_no         TEXT PRIMARY KEY,
    company_id           INTEGER NOT NULL REFERENCES company(company_id),
    form                 TEXT NOT NULL,
    filing_date          TEXT NOT NULL,      -- EDGAR filing date (ET business date)
    acceptance_datetime  TEXT NOT NULL,      -- UTC; first public availability on EDGAR
    report_date          TEXT,               -- period of report / date of event (8-K)
    items                TEXT,               -- 8-K item codes, comma separated
    primary_document     TEXT,
    primary_doc_url      TEXT,
    is_xbrl              INTEGER,
    source_doc_id        INTEGER NOT NULL REFERENCES source_document(doc_id),
    retrieved_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_filing_company ON filing(company_id, form, acceptance_datetime);

-- Canonical metric dictionary and its mapping to reported concepts.
CREATE TABLE IF NOT EXISTS metric_definition (
    metric_id         TEXT PRIMARY KEY,      -- e.g. revenue
    label             TEXT NOT NULL,
    period_type       TEXT NOT NULL,         -- duration | instant
    unit              TEXT NOT NULL,         -- USD | USD/shares | shares
    additive          INTEGER NOT NULL,      -- 1 if quarters may be summed / differenced
    accounting_basis  TEXT NOT NULL,         -- e.g. 'US GAAP, as reported'
    description       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_concept_map (
    metric_id   TEXT NOT NULL REFERENCES metric_definition(metric_id),
    taxonomy    TEXT NOT NULL,
    concept     TEXT NOT NULL,
    priority    INTEGER NOT NULL,            -- lower = preferred
    PRIMARY KEY (metric_id, taxonomy, concept)
);

-- Original reported facts (evidence).  One row per (fact, filing).  Never updated.
CREATE TABLE IF NOT EXISTS observation (
    obs_id                  INTEGER PRIMARY KEY,
    company_id              INTEGER NOT NULL REFERENCES company(company_id),
    source_kind             TEXT NOT NULL,   -- xbrl_fact | earnings_release_table
    taxonomy                TEXT NOT NULL,   -- us-gaap | dei | ifrs-full | press_release
    concept                 TEXT NOT NULL,
    accounting_definition   TEXT NOT NULL,   -- human-readable definition incl. taxonomy, period type, scope
    value                   REAL,
    unit                    TEXT NOT NULL,
    period_start            TEXT,            -- NULL for instants
    period_end              TEXT NOT NULL,   -- date the metric describes
    duration_days           INTEGER,
    fiscal_year             INTEGER,         -- computed from period_end + company FYE
    fiscal_period           TEXT,            -- Q1..Q4 | FY | YTD6 | YTD9 | INSTANT
    filing_form             TEXT,
    filing_fiscal_year      INTEGER,         -- fy/fp of the FILING (not of the fact)
    filing_fiscal_period    TEXT,
    accession_no            TEXT,
    frame                   TEXT,
    source_doc_id           INTEGER NOT NULL REFERENCES source_document(doc_id),
    source_url              TEXT NOT NULL,
    published_at            TEXT,            -- publisher timestamp (EDGAR acceptance)
    available_at            TEXT NOT NULL,   -- first-known public availability (conservative)
    availability_basis      TEXT NOT NULL,
    availability_verified   INTEGER NOT NULL,
    retrieved_at            TEXT NOT NULL,
    reliability             TEXT NOT NULL,   -- primary_regulatory_structured | primary_regulatory_unstructured | ...
    extraction_method       TEXT NOT NULL,
    natural_key             TEXT NOT NULL,
    run_id                  INTEGER REFERENCES ingest_run(run_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_obs_nk_val ON observation(natural_key, value);
CREATE INDEX IF NOT EXISTS ix_obs_lookup ON observation(company_id, concept, period_end);
CREATE INDEX IF NOT EXISTS ix_obs_avail ON observation(available_at);

-- Daily bars exactly as delivered by the vendor, with the vendor's adjustment basis recorded.
CREATE TABLE IF NOT EXISTS price_bar (
    bar_id                INTEGER PRIMARY KEY,
    company_id            INTEGER NOT NULL REFERENCES company(company_id),
    provider              TEXT NOT NULL,
    trade_date            TEXT NOT NULL,
    open REAL, high REAL, low REAL,
    close                 REAL NOT NULL,
    adj_close             REAL,
    volume                REAL,
    adjustment_basis      TEXT NOT NULL,     -- what 'close' means for this provider at retrieval time
    available_at          TEXT NOT NULL,     -- session close (16:00 ET), conservative
    availability_basis    TEXT NOT NULL,
    source_doc_id         INTEGER NOT NULL REFERENCES source_document(doc_id),
    retrieved_at          TEXT NOT NULL,
    natural_key           TEXT NOT NULL,
    run_id                INTEGER REFERENCES ingest_run(run_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_bar_nk ON price_bar(natural_key, close, volume);
CREATE INDEX IF NOT EXISTS ix_bar_lookup ON price_bar(company_id, trade_date);

CREATE TABLE IF NOT EXISTS corporate_action (
    action_id            INTEGER PRIMARY KEY,
    company_id           INTEGER NOT NULL REFERENCES company(company_id),
    action_type          TEXT NOT NULL,     -- split | cash_dividend
    ex_date              TEXT NOT NULL,
    ratio                REAL,              -- split: new shares per old share
    amount               REAL,              -- dividend per share, as delivered by provider
    amount_basis         TEXT,
    announced_at         TEXT,              -- NULL when no verifiable source
    available_at         TEXT NOT NULL,     -- conservative: ex-date open when announcement unknown
    availability_basis   TEXT NOT NULL,
    provider             TEXT NOT NULL,
    source_doc_id        INTEGER NOT NULL REFERENCES source_document(doc_id),
    retrieved_at         TEXT NOT NULL,
    natural_key          TEXT NOT NULL,
    run_id               INTEGER REFERENCES ingest_run(run_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ca_nk ON corporate_action(natural_key, ratio, amount);

-- Macro / rates observations, bitemporal: a row is stored only when the value is new as of
-- the vintage in which it was observed.
CREATE TABLE IF NOT EXISTS macro_observation (
    mobs_id               INTEGER PRIMARY KEY,
    series_id             TEXT NOT NULL,     -- e.g. FRED:DGS10, UST:10 Yr
    obs_date              TEXT NOT NULL,     -- date the value describes
    value                 REAL,              -- NULL = source reported missing ('.')
    unit                  TEXT NOT NULL,
    definition            TEXT NOT NULL,
    vintage_date          TEXT,              -- ALFRED vintage in which the value was observed
    available_at          TEXT NOT NULL,
    availability_basis    TEXT NOT NULL,
    availability_verified INTEGER NOT NULL,
    source_doc_id         INTEGER NOT NULL REFERENCES source_document(doc_id),
    source_url            TEXT NOT NULL,
    retrieved_at          TEXT NOT NULL,
    natural_key           TEXT NOT NULL,
    run_id                INTEGER REFERENCES ingest_run(run_id)
);
CREATE INDEX IF NOT EXISTS ix_macro_lookup ON macro_observation(series_id, obs_date, available_at);

CREATE TABLE IF NOT EXISTS event (
    event_id             INTEGER PRIMARY KEY,
    company_id           INTEGER REFERENCES company(company_id),
    event_type           TEXT NOT NULL,     -- earnings_release | periodic_report | material_event_8k | split_ex_date | dividend_ex_date
    event_date           TEXT NOT NULL,     -- when the event happened / date of report
    available_at         TEXT NOT NULL,     -- when the market could know it
    availability_basis   TEXT NOT NULL,
    accession_no         TEXT,
    details_json         TEXT NOT NULL,
    source_doc_id        INTEGER NOT NULL REFERENCES source_document(doc_id),
    natural_key          TEXT NOT NULL UNIQUE,
    run_id               INTEGER REFERENCES ingest_run(run_id)
);

-- Document text for retrieval. Chunks are derived from source_document bytes (re-derivable).
CREATE TABLE IF NOT EXISTS document_chunk (
    chunk_id      INTEGER PRIMARY KEY,
    doc_id        INTEGER NOT NULL REFERENCES source_document(doc_id),
    seq           INTEGER NOT NULL,
    section       TEXT,
    char_start    INTEGER NOT NULL,
    char_end      INTEGER NOT NULL,
    text          TEXT NOT NULL,
    UNIQUE (doc_id, seq)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text, content='document_chunk', content_rowid='chunk_id', tokenize='porter unicode61'
);

-- Calculated metrics.  Always tied to the as-of cutoff they were computed for, with lineage.
CREATE TABLE IF NOT EXISTS derived_metric (
    derived_id       INTEGER PRIMARY KEY,
    snapshot_id      INTEGER REFERENCES snapshot(snapshot_id),
    company_id       INTEGER REFERENCES company(company_id),
    metric_id        TEXT NOT NULL,
    value            REAL,
    unit             TEXT NOT NULL,
    period_start     TEXT,
    period_end       TEXT,
    fiscal_year      INTEGER,
    fiscal_period    TEXT,
    methodology      TEXT NOT NULL,
    inputs_json      TEXT NOT NULL,       -- [{"table":"observation","id":..}, ...]
    available_at     TEXT NOT NULL,       -- max(available_at of inputs)
    as_of_cutoff     TEXT NOT NULL,
    computed_at      TEXT NOT NULL,
    code_version     TEXT NOT NULL
);

-- Reserved for later sprints: analyst / agent judgments, kept apart from evidence.
CREATE TABLE IF NOT EXISTS analyst_judgment (
    judgment_id     INTEGER PRIMARY KEY,
    company_id      INTEGER REFERENCES company(company_id),
    author          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    as_of_cutoff    TEXT NOT NULL,
    statement       TEXT NOT NULL,
    evidence_json   TEXT NOT NULL         -- citations to observation / chunk ids
);

CREATE TABLE IF NOT EXISTS qa_result (
    qa_id        INTEGER PRIMARY KEY,
    run_id       INTEGER REFERENCES ingest_run(run_id),
    check_name   TEXT NOT NULL,
    severity     TEXT NOT NULL,           -- pass | info | warn | fail
    company_id   INTEGER REFERENCES company(company_id),
    subject      TEXT,
    detail_json  TEXT NOT NULL,
    checked_at   TEXT NOT NULL
);

-- Gaps are recorded, never filled with estimates.
CREATE TABLE IF NOT EXISTS data_gap (
    gap_id       INTEGER PRIMARY KEY,
    company_id   INTEGER REFERENCES company(company_id),
    scope        TEXT NOT NULL,
    description  TEXT NOT NULL,
    detected_at  TEXT NOT NULL,
    run_id       INTEGER REFERENCES ingest_run(run_id)
);

CREATE TABLE IF NOT EXISTS snapshot (
    snapshot_id      INTEGER PRIMARY KEY,
    cutoff           TEXT NOT NULL,       -- market-time cutoff (UTC)
    knowledge_time   TEXT NOT NULL,       -- system-time cutoff on retrieved_at (UTC)
    created_at       TEXT NOT NULL,
    code_version     TEXT NOT NULL,
    content_sha256   TEXT NOT NULL,       -- hash of canonical JSON content
    path             TEXT NOT NULL,
    item_count       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot_item (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshot(snapshot_id),
    table_name    TEXT NOT NULL,
    row_id        INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, table_name, row_id)
);

-- Append-only enforcement.
CREATE TRIGGER IF NOT EXISTS ro_source_document_u BEFORE UPDATE ON source_document BEGIN SELECT RAISE(ABORT, 'source_document is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_source_document_d BEFORE DELETE ON source_document BEGIN SELECT RAISE(ABORT, 'source_document is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_observation_u BEFORE UPDATE ON observation BEGIN SELECT RAISE(ABORT, 'observation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_observation_d BEFORE DELETE ON observation BEGIN SELECT RAISE(ABORT, 'observation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_price_bar_u BEFORE UPDATE ON price_bar BEGIN SELECT RAISE(ABORT, 'price_bar is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_price_bar_d BEFORE DELETE ON price_bar BEGIN SELECT RAISE(ABORT, 'price_bar is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_ca_u BEFORE UPDATE ON corporate_action BEGIN SELECT RAISE(ABORT, 'corporate_action is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_ca_d BEFORE DELETE ON corporate_action BEGIN SELECT RAISE(ABORT, 'corporate_action is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_macro_u BEFORE UPDATE ON macro_observation BEGIN SELECT RAISE(ABORT, 'macro_observation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_macro_d BEFORE DELETE ON macro_observation BEGIN SELECT RAISE(ABORT, 'macro_observation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_filing_u BEFORE UPDATE ON filing BEGIN SELECT RAISE(ABORT, 'filing is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_filing_d BEFORE DELETE ON filing BEGIN SELECT RAISE(ABORT, 'filing is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_event_u BEFORE UPDATE ON event BEGIN SELECT RAISE(ABORT, 'event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_event_d BEFORE DELETE ON event BEGIN SELECT RAISE(ABORT, 'event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_snapshot_u BEFORE UPDATE ON snapshot BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ro_snapshot_d BEFORE DELETE ON snapshot BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ro_snapshot_item_u BEFORE UPDATE ON snapshot_item BEGIN SELECT RAISE(ABORT, 'snapshot_item is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ro_snapshot_item_d BEFORE DELETE ON snapshot_item BEGIN SELECT RAISE(ABORT, 'snapshot_item is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ro_derived_u BEFORE UPDATE ON derived_metric BEGIN SELECT RAISE(ABORT, 'derived_metric is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_derived_d BEFORE DELETE ON derived_metric BEGIN SELECT RAISE(ABORT, 'derived_metric is append-only'); END;

-- ---------------------------------------------------------------- v2: expectations & event reactions
-- Curated expectation claims (guidance, consensus). Each claim cites a stored source document and a
-- verbatim quote that is machine-checked against the stored bytes.  Append-only.
CREATE TABLE IF NOT EXISTS expectation (
    expectation_id        INTEGER PRIMARY KEY,
    company_id            INTEGER NOT NULL REFERENCES company(company_id),
    event_key             TEXT NOT NULL,       -- e.g. MSFT:FY2024Q2
    expectation_type      TEXT NOT NULL,       -- management_guidance | sell_side_consensus | no_guidance_policy
    metric                TEXT NOT NULL,
    period_end            TEXT,
    value_low             REAL,
    value_high            REAL,
    value_point           REAL,
    unit                  TEXT,
    provider              TEXT NOT NULL,       -- who produced the expectation (e.g. LSEG, Microsoft management)
    reported_by           TEXT,                -- who published it (e.g. CNBC)
    source_doc_id         INTEGER REFERENCES source_document(doc_id),
    source_url            TEXT NOT NULL,
    source_published_at   TEXT,                -- as stated by the source (UTC) or NULL
    published_at_basis    TEXT NOT NULL,
    verbatim_quote        TEXT,
    quote_verified        INTEGER NOT NULL,    -- 1 if the quote was found in the stored source bytes
    provenance_grade      TEXT NOT NULL,       -- A | B | C | D (see docs/EVENT_ENGINE.md)
    available_at          TEXT,                -- usable point-in-time from here; NULL = never usable PIT
    notes                 TEXT,
    retrieved_at          TEXT NOT NULL,
    natural_key           TEXT NOT NULL,
    run_id                INTEGER REFERENCES ingest_run(run_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_exp_nk ON expectation(natural_key, quote_verified, source_doc_id);
CREATE TRIGGER IF NOT EXISTS ro_exp_u BEFORE UPDATE ON expectation BEGIN SELECT RAISE(ABORT, 'expectation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_exp_d BEFORE DELETE ON expectation BEGIN SELECT RAISE(ABORT, 'expectation is append-only'); END;

-- Measured reactions (derived). One row per (event, computation); full detail in result_json.
CREATE TABLE IF NOT EXISTS event_reaction (
    reaction_id       INTEGER PRIMARY KEY,
    event_id          INTEGER NOT NULL REFERENCES event(event_id),
    company_id        INTEGER NOT NULL REFERENCES company(company_id),
    reaction_day      TEXT NOT NULL,           -- first trading session that could react (d0)
    timing            TEXT NOT NULL,           -- after_close | pre_market | intraday | non_trading_day
    car_d0_mm         REAL,                    -- market-model abnormal return, d0 close-to-close vs SPY
    attribution       TEXT NOT NULL,           -- low | medium | high uncertainty
    result_json       TEXT NOT NULL,
    data_through      TEXT NOT NULL,           -- last trade date used
    knowledge_time    TEXT NOT NULL,
    computed_at       TEXT NOT NULL,
    code_version      TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ro_react_u BEFORE UPDATE ON event_reaction BEGIN SELECT RAISE(ABORT, 'event_reaction is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_react_d BEFORE DELETE ON event_reaction BEGIN SELECT RAISE(ABORT, 'event_reaction is append-only'); END;

-- Errors found in stored evidence are never edited away: a correction row supersedes the faulty field.
CREATE TABLE IF NOT EXISTS data_correction (
    correction_id   INTEGER PRIMARY KEY,
    table_name      TEXT NOT NULL,
    row_filter      TEXT NOT NULL,          -- SQL predicate identifying the affected rows
    field           TEXT NOT NULL,
    wrong_value     TEXT,
    correct_value   TEXT,
    reason          TEXT NOT NULL,
    evidence        TEXT,                   -- how the error was established
    recorded_at     TEXT NOT NULL,
    code_version    TEXT NOT NULL,
    UNIQUE (table_name, row_filter, field, correct_value)
);
CREATE TRIGGER IF NOT EXISTS ro_corr_u BEFORE UPDATE ON data_correction BEGIN SELECT RAISE(ABORT, 'data_correction is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_corr_d BEFORE DELETE ON data_correction BEGIN SELECT RAISE(ABORT, 'data_correction is append-only'); END;

-- ---------------------------------------------------------------- v3: Expectations Engine
-- One row per expectation datum as it existed at `as_of` (point-in-time). Append-only; revisions are new rows
-- linked by (company, class, provider, metric, target_period). Classes are kept strictly apart:
--   reported_result | management_guidance | analyst_consensus | market_implied | narrative
CREATE TABLE IF NOT EXISTS expectation_obs (
    eo_id             INTEGER PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES company(company_id),
    class             TEXT NOT NULL,
    provider          TEXT NOT NULL,       -- e.g. FactSet via MarketWatch, Zacks, Meta management, CBOE (VXGOG)
    metric            TEXT NOT NULL,       -- eps, revenue, total_expenses, capex, azure_growth_cc, implied_move...
    basis             TEXT,                -- e.g. 'street (may exclude one-offs)', 'GAAP', 'constant currency'
    target_period     TEXT,                -- fiscal label FY2024Q2 / FY2024, or an event key for market-implied
    stat              TEXT NOT NULL,       -- point | low | high | mean | n_estimates | text
    value             REAL,
    value_text        TEXT,
    unit              TEXT,
    as_of             TEXT NOT NULL,       -- when this expectation is evidenced to exist (UTC)
    as_of_basis       TEXT NOT NULL,       -- wayback_capture_timestamp | sec_acceptance | page_metadata | computed
    available_at      TEXT,                -- usable point-in-time from here; NULL = not usable PIT
    provenance_grade  TEXT NOT NULL,       -- A | B | C | D
    source_doc_id     INTEGER REFERENCES source_document(doc_id),
    source_url        TEXT,
    locator           TEXT,                -- quote or page section the value was read from
    method            TEXT NOT NULL,       -- parser / model name and version
    adjustments       TEXT,                -- e.g. split rebasing applied, with factor
    checks_json       TEXT,                -- automated validation results for this datum
    notes             TEXT,
    created_at        TEXT NOT NULL,
    natural_key       TEXT NOT NULL,
    run_id            INTEGER REFERENCES ingest_run(run_id)
);
-- NULL-safe uniqueness (plain UNIQUE treats NULLs as distinct, which silently disabled deduplication in v3.0)
CREATE UNIQUE INDEX IF NOT EXISTS ux_eo_nk2 ON expectation_obs(natural_key, coalesce(value, -1e308), coalesce(value_text, ''));
CREATE INDEX IF NOT EXISTS ix_eo_lookup ON expectation_obs(company_id, class, metric, target_period, as_of);
CREATE TRIGGER IF NOT EXISTS ro_eo_u BEFORE UPDATE ON expectation_obs BEGIN SELECT RAISE(ABORT, 'expectation_obs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ro_eo_d BEFORE DELETE ON expectation_obs BEGIN SELECT RAISE(ABORT, 'expectation_obs is append-only'); END;
