"""Headline GAAP figures from earnings-release exhibits (8-K Item 2.02, EX-99.1).

Why: between the earnings release and the 10-Q/10-K, the quarter's numbers are public but not
yet in XBRL.  A point-in-time snapshot in that window must use the release.

How: only the GAAP *income statement* table is used — the first table whose header row is
made solely of years and which has exact 'Revenue(s)'/'Total revenue(s)' and 'Net income' rows.
Values are taken from the most recent year within the first (three-month) column group.

Trust: these observations are 'primary_issuer_unstructured'; QA reconciles every one against
the later XBRL fact for the same period (check `earnings_release_vs_xbrl`).
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

from bs4 import BeautifulSoup

from ..db import record_gap
from ..fiscal import fiscal_label

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
PERIOD_RE = re.compile(r"(?:Three Months|Quarter) Ended\s+([A-Z][a-z]+)\s+(\d{1,2})", re.I)
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
NUM_RE = re.compile(r"^\(?-?[\d,]*\.?\d+\)?$")

ROW_PATTERNS = {
    "revenue": re.compile(r"^(total )?revenues?$", re.I),
    "operating_income": re.compile(r"^(income from operations|operating income)$", re.I),
    "net_income": re.compile(r"^net income$", re.I),
}
SCALES = {"millions": 1e6, "thousands": 1e3, "billions": 1e9}


def _cells(tr):
    return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]


def _num(tok: str):
    t = tok.replace("$", "").replace(",", "").strip()
    if not NUM_RE.match(tok.replace("$", "").strip()):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _numbers(cells):
    out = []
    for c in cells[1:]:
        c = c.strip()
        if c in ("$", "%", ""):
            continue
        if c in ("-", "—", "–"):
            out.append(None)
            continue
        v = _num(c)
        if v is None:
            return None  # non-numeric content: not a plain data row
        out.append(v)
    return out


def parse_release(body: bytes):
    """Return dict with period_end, scale_word and {metric: (value, row_label)} or None."""
    soup = BeautifulSoup(body, "lxml")
    full_text = soup.get_text(" ")
    for tbl in soup.find_all("table"):
        rows = [[c for c in _cells(tr) if c] for tr in tbl.find_all("tr")]
        rows = [r for r in rows if r]
        hdr_idx = next((i for i, r in enumerate(rows) if len(r) >= 2 and all(YEAR_RE.match(c) for c in r)), None)
        if hdr_idx is None:
            continue
        labels = [r[0] for r in rows]
        if not any(ROW_PATTERNS["revenue"].match(l) for l in labels) or not any(
                ROW_PATTERNS["net_income"].match(l) for l in labels):
            continue
        years = [int(y) for y in rows[hdr_idx]]
        group = []
        for y in years:
            if y in group:
                break
            group.append(y)
        cur_idx = group.index(max(group))
        tbl_text = tbl.get_text(" ")
        m = PERIOD_RE.search(tbl_text) or PERIOD_RE.search(full_text)
        if not m or m.group(1).lower() not in MONTHS:
            continue
        month, day = MONTHS[m.group(1).lower()], int(m.group(2))
        period_end = date(max(group), month, min(day, monthrange(max(group), month)[1]))
        scale_m = re.search(r"in (millions|thousands|billions)", tbl_text, re.I) or \
            re.search(r"in (millions|thousands|billions)", full_text, re.I)
        scale_word = scale_m.group(1).lower() if scale_m else None
        found = {}
        eps_zone = False
        for r in rows[hdr_idx + 1:]:
            label = r[0]
            if re.search(r"(earnings|net income) per (common )?share", label, re.I) and len(r) == 1:
                eps_zone = True
                continue
            nums = _numbers(r)
            if not nums or len(nums) < len(group):
                continue
            val = nums[cur_idx]
            if val is None:
                continue
            for metric, pat in ROW_PATTERNS.items():
                if metric not in found and pat.match(label):
                    found[metric] = (val, label)
            is_diluted_eps = (re.match(r"^diluted$", label, re.I) and eps_zone) or (
                re.match(r"^diluted\b", label, re.I) and re.search(r"\bEPS\b|per (common )?share", label, re.I)
                and not re.search(r"shares used|calculation|weighted", label, re.I))
            # EPS rows carry decimals; share-count rows ('...per share calculation') do not match the pattern
            if "eps_diluted" not in found and is_diluted_eps and "." in " ".join(r[1:]):
                found["eps_diluted"] = (val, label)
                eps_zone = False
        if "revenue" in found and "net_income" in found:
            return {"period_end": period_end, "scale_word": scale_word, "years": group, "values": found}
    return None


def ingest_releases(conn, company, run_id, stats, store) -> int:
    """Parse the stored EX-99.x exhibits of 8-K Item 2.02 filings. Re-running is idempotent: identical values
    are ignored by the natural-key index; a changed parser output is appended (and flagged by QA)."""
    rows = conn.execute(
        """SELECT d.doc_id, d.url, d.accession_no, d.retrieved_at, f.acceptance_datetime, f.form
           FROM source_document d JOIN filing f ON f.accession_no=d.accession_no
           WHERE d.company_id=? AND d.doc_type LIKE 'EX-99%' AND f.form='8-K' AND f.items LIKE '%2.02%'
           ORDER BY d.accession_no, d.doc_type""",
        (company["company_id"],)).fetchall()
    fye = company["fiscal_year_end"]
    n = 0
    done, failed = set(), {}
    for r in rows:
        if r["accession_no"] in done:
            continue
        parsed = parse_release(store.read(r["doc_id"]))
        if not parsed:
            failed.setdefault(r["accession_no"], []).append(r["url"])
            continue
        done.add(r["accession_no"])
        scale = SCALES.get(parsed["scale_word"] or "", None)
        pe = parsed["period_end"]
        ps = _quarter_start(pe)
        fy, fp = fiscal_label(ps, pe, fye)
        for metric, (val, label) in parsed["values"].items():
            if metric == "eps_diluted":
                v, unit = val, "USD/shares"
            else:
                if scale is None:
                    continue
                v, unit = val * scale, "USD"
            defn = (f"GAAP '{label}' as presented in the earnings-release income statement table (EX-99.1), "
                    f"three months ended {pe.isoformat()}; unaudited; scale '{parsed['scale_word']}' applied")
            nk = f"release|{company['cik']}|{metric}|{pe.isoformat()}|{r['accession_no']}"
            cur = conn.execute(
                """INSERT OR IGNORE INTO observation(company_id, source_kind, taxonomy, concept, accounting_definition,
                     value, unit, period_start, period_end, duration_days, fiscal_year, fiscal_period, filing_form,
                     accession_no, source_doc_id, source_url, published_at, available_at, availability_basis,
                     availability_verified, retrieved_at, reliability, extraction_method, natural_key, run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (company["company_id"], "earnings_release_table", "press_release", metric, defn, v, unit,
                 ps.isoformat(), pe.isoformat(), (pe - ps).days, fy, fp, r["form"], r["accession_no"], r["doc_id"],
                 r["url"], r["acceptance_datetime"], r["acceptance_datetime"], "sec_acceptance_datetime_of_8k", 1,
                 r["retrieved_at"], "primary_issuer_unstructured", "html_table_parser_v1", nk, run_id))
            n += cur.rowcount
    for acc, urls in failed.items():
        if acc not in done:
            stats.setdefault("release_parse_failures", []).extend(urls)
            record_gap(conn, company["company_id"], "earnings_release",
                       f"no GAAP income-statement table parsed from 8-K {acc} exhibits {urls}", run_id)
    stats[f"release_obs_new_{company['ticker']}"] = n
    return n


def _quarter_start(pe: date) -> date:
    """First day of the three-month period ending on pe (calendar-month quarters)."""
    m = pe.month - 2
    y = pe.year
    if m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)
