"""Scheduled macro events with defensible timestamps.

* FOMC statements: dates from the Federal Reserve's calendar pages; the release time is read from each
  statement page ("For release at 2:00 p.m. EST"), so it is verified per event.
* Data releases (CPI, Employment Situation, GDP): dates are ALFRED vintage dates (a vintage date can also
  be a revision-only date, e.g. annual seasonal-factor updates); the time is the agency's standard
  08:30 ET release time and is flagged as assumed.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from ..config import MACRO_RELEASE_SERIES
from ..evidence import FetchError
from ..timeutil import et_to_utc_str

FED = "https://www.federalreserve.gov"
FOMC_CALENDAR = FED + "/monetarypolicy/fomccalendars.htm"
FOMC_HISTORICAL = FED + "/monetarypolicy/fomchistorical{year}.htm"
STATEMENT_RE = re.compile(r"/newsevents/pressreleases/(monetary(\d{8})a\.htm)")
RELEASE_TIME_RE = re.compile(r"For release at\s+(\d{1,2}):(\d{2})\s*([ap])\.?m\.?\s*(EST|EDT|ET)?", re.I)
DECISION_RE = re.compile(r"(decided(?: today)? to|voted to)\s+(maintain|keep|raise|lower|reduce)\s+the target range for "
                         r"the federal funds rate(?: by [^.]*? percentage points?,?)?\s+(?:at|to)\s+"
                         r"(\d[\d/\s-]*\s*to\s*\d[\d/\s-]*) percent", re.I)
ALFRED_VINTAGES = "https://alfred.stlouisfed.org/series/downloaddata?seid={sid}"


def _insert_event(conn, etype, event_date, available_at, basis, details, doc_id, nk, run_id) -> int:
    cur = conn.execute(
        """INSERT OR IGNORE INTO event(company_id, event_type, event_date, available_at, availability_basis,
             accession_no, details_json, source_doc_id, natural_key, run_id) VALUES (NULL,?,?,?,?,NULL,?,?,?,?)""",
        (etype, event_date, available_at, basis, json.dumps(details, sort_keys=True), doc_id, nk, run_id))
    return cur.rowcount


def statement_links(html: str):
    return sorted({(m.group(1), m.group(2)) for m in STATEMENT_RE.finditer(html)})


def parse_statement(html: str):
    from .documents import html_to_text
    text = html_to_text(html.encode() if isinstance(html, str) else html)
    m = RELEASE_TIME_RE.search(text)
    text = text.replace("\u2011", "-").replace("\u2010", "-")
    t = None
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if m.group(3).lower() == "p" and hh != 12:
            hh += 12
        t = (hh, mm, (m.group(4) or "").upper())
    d = DECISION_RE.search(text)
    decision = None
    if d:
        action = {"keep": "maintain", "reduce": "lower"}.get(d.group(2).lower(), d.group(2).lower())
        decision = {"action": action, "range_text": re.sub(r"\s+", " ", d.group(3)).strip()}
    return t, decision


def fomc_decision(conn, store, event_row):
    """Policy decision re-derived from the stored statement bytes (authoritative over details_json, which
    older parser versions may have left empty). Returns (decision or None, release_kind)."""
    body = store.read(event_row["source_doc_id"])
    _, decision = parse_statement(body)
    return decision, ("policy_decision" if decision else "other_fomc_or_fed_release")


def ingest_fomc(conn, store, years, run_id, stats) -> int:
    pages = [FOMC_CALENDAR] + [FOMC_HISTORICAL.format(year=y) for y in years if y <= date.today().year - 5]
    links = set()
    for url in pages:
        try:
            f = store.fetch(url, source_system="federal_reserve", doc_type="fomc_calendar_html", run_id=run_id,
                            title="FOMC calendar")
        except FetchError as e:
            stats.setdefault("fomc_errors", []).append(str(e))
            continue
        links |= set(statement_links(f.body.decode("utf-8", "replace")))
    n = 0
    wanted = {str(y) for y in years}
    for fname, ymd in sorted(links):
        if ymd[:4] not in wanted:
            continue
        nk = f"fomc_statement|{ymd}"
        if conn.execute("SELECT 1 FROM event WHERE natural_key=?", (nk,)).fetchone():
            continue
        url = f"{FED}/newsevents/pressreleases/{fname}"
        f = store.fetch(url, source_system="federal_reserve", doc_type="fomc_statement_html", run_id=run_id,
                        title=f"FOMC statement {ymd}")
        t, decision = parse_statement(f.body)
        d = datetime.strptime(ymd, "%Y%m%d").date()
        if t:
            avail, basis = et_to_utc_str(d, t[0], t[1]), f"fed_press_release_stated_time ({t[2] or 'ET'})"
        else:
            # no stated time: conservative end of day
            avail, basis = et_to_utc_str(d, 23, 59, 59), "fed_press_release_date_only_end_of_day"
            stats.setdefault("fomc_no_time", []).append(url)
        n += _insert_event(conn, "fomc_statement", d.isoformat(), avail, basis,
                           {"url": url, "decision": decision, "stated_release_time": t,
                            "note": "Chair press conference typically follows 30 minutes later"},
                           f.doc_id, nk, run_id)
    stats["fomc_events_new"] = n
    return n


def ingest_macro_releases(conn, store, since: str, run_id, stats) -> int:
    n = 0
    for sid, (etype, label, hh, mm) in MACRO_RELEASE_SERIES.items():
        f = store.fetch(ALFRED_VINTAGES.format(sid=sid), source_system="alfred", doc_type="alfred_vintage_list_html",
                        run_id=run_id, title=f"ALFRED vintage dates {sid}")
        vintages = sorted(set(re.findall(r'<option value="(\d{4}-\d{2}-\d{2})"', f.body.decode("utf-8", "replace"))))
        for v in vintages:
            if v < since:
                continue
            n += _insert_event(conn, etype, v, et_to_utc_str(date.fromisoformat(v), hh, mm),
                               "alfred_vintage_date + agency_standard_release_time_assumed",
                               {"series": sid, "label": label, "note": "ALFRED vintage date; may be a revision-only "
                                "vintage (e.g. seasonal factors) rather than a new-data release"},
                               f.doc_id, f"alfred_vintage|{sid}|{v}", run_id)
    stats["macro_release_events_new"] = n
    return n
