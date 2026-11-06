"""SEC EDGAR: filing index (submissions API) and XBRL company facts.

Point-in-time rule: an XBRL fact becomes available at the EDGAR acceptance timestamp of the
filing that contains it (accession number). Restated values arrive as new facts with a new
accession and are stored as separate observations; nothing is overwritten.
"""
from __future__ import annotations

import json
from datetime import date

from ..db import record_gap, set_fiscal_year_end
from ..evidence import EvidenceStore
from ..fiscal import classify_duration, fiscal_label
from ..timeutil import edgar_acceptance_to_utc, et_to_utc_str

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"

PERIODIC_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def archive_url(cik: str, accession: str, filename: str = "") -> str:
    return ARCHIVE_BASE.format(cik_int=int(cik), acc_nodash=accession.replace("-", "")) + filename


def ingest_filings(conn, store: EvidenceStore, company, run_id, stats) -> int:
    f = store.fetch(SUBMISSIONS_URL.format(cik=company["cik"]), source_system="sec_xbrl_api",
                    doc_type="submissions_json", run_id=run_id, company_id=company["company_id"],
                    title=f"EDGAR submissions {company['ticker']}")
    data = json.loads(f.body)
    set_fiscal_year_end(conn, company["company_id"], data.get("fiscalYearEnd") or "1231")
    pages = [(f, data["filings"]["recent"])]
    for extra in data["filings"].get("files", []):
        g = store.fetch(SUBMISSIONS_PAGE_URL.format(name=extra["name"]), source_system="sec_xbrl_api",
                        doc_type="submissions_json", run_id=run_id, company_id=company["company_id"])
        pages.append((g, json.loads(g.body)))
    n = 0
    for doc, cols in pages:
        n += _insert_filings(conn, company, doc, cols)
    stats[f"filings_new_{company['ticker']}"] = n
    return n


def _insert_filings(conn, company, doc, cols) -> int:
    n = 0
    for i, acc in enumerate(cols["accessionNumber"]):
        prim = cols["primaryDocument"][i] or None
        cur = conn.execute(
            """INSERT OR IGNORE INTO filing(accession_no, company_id, form, filing_date, acceptance_datetime,
                 report_date, items, primary_document, primary_doc_url, is_xbrl, source_doc_id, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (acc, company["company_id"], cols["form"][i], cols["filingDate"][i],
             edgar_acceptance_to_utc(cols["acceptanceDateTime"][i]), cols["reportDate"][i] or None,
             cols["items"][i] or None, prim, archive_url(company["cik"], acc, prim) if prim else None,
             int(cols["isXBRL"][i] or 0), doc.doc_id, doc.retrieved_at))
        n += cur.rowcount
    return n


def ingest_companyfacts(conn, store: EvidenceStore, company, run_id, stats) -> int:
    """Store every non-dimensional fact in SEC companyfacts as an observation."""
    company = conn.execute("SELECT * FROM company WHERE company_id=?", (company["company_id"],)).fetchone()
    url = COMPANYFACTS_URL.format(cik=company["cik"])
    f = store.fetch(url, source_system="sec_xbrl_api", doc_type="companyfacts_json", run_id=run_id,
                    company_id=company["company_id"], title=f"XBRL companyfacts {company['ticker']}")
    return load_companyfacts(conn, company, f.doc_id, f.body, f.retrieved_at, run_id, stats)


def load_companyfacts(conn, company, doc_id, body: bytes, retrieved_at, run_id, stats) -> int:
    data = json.loads(body)
    acceptance = {r["accession_no"]: r["acceptance_datetime"] for r in conn.execute(
        "SELECT accession_no, acceptance_datetime FROM filing WHERE company_id=?", (company["company_id"],))}
    fye = company["fiscal_year_end"] if company["fiscal_year_end"] != "0000" else "1231"
    rows, missing_acc = [], set()
    for tax, concepts in data.get("facts", {}).items():
        for concept, spec in concepts.items():
            label = spec.get("label") or concept
            for unit, facts in spec.get("units", {}).items():
                for fct in facts:
                    rows.append(_fact_row(company, tax, concept, label, unit, fct, acceptance, fye, doc_id,
                                          retrieved_at, run_id, missing_acc))
    before = conn.total_changes
    conn.executemany(
        """INSERT OR IGNORE INTO observation(company_id, source_kind, taxonomy, concept, accounting_definition, value,
             unit, period_start, period_end, duration_days, fiscal_year, fiscal_period, filing_form, filing_fiscal_year,
             filing_fiscal_period, accession_no, frame, source_doc_id, source_url, published_at, available_at,
             availability_basis, availability_verified, retrieved_at, reliability, extraction_method, natural_key,
             run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    n = conn.total_changes - before
    stats[f"xbrl_facts_seen_{company['ticker']}"] = len(rows)
    stats[f"xbrl_facts_new_{company['ticker']}"] = n
    if missing_acc:
        record_gap(conn, company["company_id"], "sec_xbrl",
                   f"{len(missing_acc)} accession(s) in companyfacts not found in submissions index; availability "
                   f"falls back to end of EDGAR filing date (unverified). e.g. {sorted(missing_acc)[:3]}", run_id)
    return n


def _fact_row(company, tax, concept, label, unit, fct, acceptance, fye, doc_id, retrieved_at, run_id, missing_acc):
    start, end, accn = fct.get("start"), fct["end"], fct.get("accn")
    ps = date.fromisoformat(start) if start else None
    pe = date.fromisoformat(end)
    days = (pe - ps).days if ps else None
    fy, fp = fiscal_label(ps, pe, fye)
    if accn in acceptance:
        avail, basis, verified, published = acceptance[accn], "sec_acceptance_datetime", 1, acceptance[accn]
    else:
        missing_acc.add(accn)
        avail = et_to_utc_str(date.fromisoformat(fct["filed"]), 23, 59, 59)
        basis, verified, published = "edgar_filing_date_end_of_day", 0, None
    defn = (f"{tax}:{concept} — {label}; {'duration ' + str(days) + 'd' if ps else 'instant'}; unit {unit}; "
            f"consolidated entity, no dimensional members (companyfacts excludes dimensional facts)")
    nk = f"xbrl|{company['cik']}|{tax}|{concept}|{unit}|{start or ''}|{end}|{accn}"
    url = archive_url(company["cik"], accn) if accn else COMPANYFACTS_URL.format(cik=company["cik"])
    return (company["company_id"], "xbrl_fact", tax, concept, defn, fct.get("val"), unit, start, end, days, fy, fp,
            fct.get("form"), fct.get("fy"), fct.get("fp"), accn, fct.get("frame"), doc_id, url, published, avail,
            basis, verified, retrieved_at, "primary_regulatory_structured", "sec_companyfacts_api", nk, run_id)


def duration_kind(days):
    return classify_duration(days)
