"""SQLite connection, schema bootstrap and ingest-run bookkeeping."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from .config import BENCHMARKS, CONTEXT_ISSUERS, DB_PATH, METRICS, UNIVERSE
from .timeutil import utcnow

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


SCHEMA_VERSION = 2


def _migrate_company_v2(conn) -> None:
    """v1 -> v2: company.cik becomes nullable and gains kind/role. Rebuilt in place (rows and ids kept),
    so existing evidence, snapshots and their hashes are untouched."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(company)")]
    if "role" in cols:
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        BEGIN;
        CREATE TABLE company_v2 (
            company_id INTEGER PRIMARY KEY, ticker TEXT NOT NULL UNIQUE, cik TEXT UNIQUE, name TEXT NOT NULL,
            fiscal_year_end TEXT NOT NULL, exchange TEXT, kind TEXT NOT NULL DEFAULT 'issuer',
            role TEXT NOT NULL DEFAULT 'universe');
        INSERT INTO company_v2(company_id, ticker, cik, name, fiscal_year_end, exchange)
            SELECT company_id, ticker, cik, name, fiscal_year_end, exchange FROM company;
        DROP TABLE company;
        ALTER TABLE company_v2 RENAME TO company;
        COMMIT;""")
    conn.execute("PRAGMA foreign_keys = ON")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"foreign key check failed after migration: {bad[:5]}")


def init_db(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='company'").fetchone():
        _migrate_company_v2(conn)
    conn.executescript(SCHEMA_PATH.read_text())
    for c in UNIVERSE:
        conn.execute(
            "INSERT OR IGNORE INTO company(ticker, cik, name, fiscal_year_end, exchange, kind, role) "
            "VALUES (?,?,?,?,?,'issuer','universe')", (c.ticker, c.cik, c.name, "0000", c.exchange))
    for c in CONTEXT_ISSUERS:
        conn.execute(
            "INSERT OR IGNORE INTO company(ticker, cik, name, fiscal_year_end, exchange, kind, role) "
            "VALUES (?,?,?,?,?,'issuer','context')", (c.ticker, c.cik, c.name, "0000", c.exchange))
    for b in BENCHMARKS:
        conn.execute(
            "INSERT OR IGNORE INTO company(ticker, cik, name, fiscal_year_end, exchange, kind, role) "
            "VALUES (?,NULL,?,'0000',NULL,?,'benchmark')", (b.ticker, b.name, b.kind))
    for mid, m in METRICS.items():
        conn.execute(
            "INSERT OR REPLACE INTO metric_definition VALUES (?,?,?,?,?,?,?)",
            (mid, m["label"], m["period_type"], m["unit"], m["additive"],
             "US GAAP as reported in filer XBRL (no harmonisation)", m["description"]),
        )
        for prio, qc in enumerate(m["concepts"]):
            tax, concept = qc.split(":")
            conn.execute("INSERT OR REPLACE INTO metric_concept_map VALUES (?,?,?,?)", (mid, tax, concept, prio))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def set_fiscal_year_end(conn, company_id: int, fye: str) -> None:
    # company is reference data (not evidence); FYE is filled from EDGAR submissions.
    conn.execute("UPDATE company SET fiscal_year_end=? WHERE company_id=?", (fye, company_id))


def companies(conn, tickers=None, role="universe"):
    rows = conn.execute("SELECT * FROM company WHERE role=? ORDER BY company_id", (role,)).fetchall()
    if tickers:
        wanted = {t.upper() for t in tickers}
        rows = [r for r in rows if r["ticker"] in wanted]
    return rows


@contextmanager
def ingest_run(conn, component: str, params: dict):
    cur = conn.execute(
        "INSERT INTO ingest_run(component, params_json, code_version, started_at) VALUES (?,?,?,?)",
        (component, json.dumps(params, sort_keys=True, default=str), __version__, utcnow()),
    )
    run_id = cur.lastrowid
    conn.commit()
    stats: dict = {}
    try:
        yield run_id, stats
    except Exception as e:
        conn.rollback()
        conn.execute("UPDATE ingest_run SET status='failed', finished_at=?, notes=?, stats_json=? WHERE run_id=?",
                     (utcnow(), repr(e)[:2000], json.dumps(stats), run_id))
        conn.commit()
        raise
    conn.execute("UPDATE ingest_run SET status='ok', finished_at=?, stats_json=? WHERE run_id=?",
                 (utcnow(), json.dumps(stats, sort_keys=True), run_id))
    conn.commit()


def record_gap(conn, company_id, scope: str, description: str, run_id=None) -> None:
    exists = conn.execute(
        "SELECT 1 FROM data_gap WHERE coalesce(company_id,-1)=coalesce(?,-1) AND scope=? AND description=?",
        (company_id, scope, description)).fetchone()
    if not exists:
        conn.execute("INSERT INTO data_gap(company_id, scope, description, detected_at, run_id) VALUES (?,?,?,?,?)",
                     (company_id, scope, description, utcnow(), run_id))
