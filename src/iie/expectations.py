"""Expectations registry: curated guidance and consensus claims, verified against stored sources.

A claim is a row of `registry/expectations.json`. Ingestion fetches its source URL into the evidence
store, checks that the verbatim quote occurs in the page text, establishes the publication time
and grades provenance:

  A  primary issuer document with a regulator-verified timestamp before the event (EDGAR 8-K)
  B  dated source published before the event (issuer IR page/call date, or a dated press preview);
     timestamp as stated by the source, cross-checked with page metadata when present
  C  source published AFTER the event that reports a pre-event expectation (e.g. "vs. LSEG consensus");
     the figure may be genuine but its point-in-time provenance cannot be verified
  D  quote not found in the stored source, or no usable date

Only A and B carry an `available_at` and may be used point-in-time. C is shown for description only and
labelled as such. D is recorded and never used.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .config import ROOT
from .evidence import FetchError
from .ingest.documents import html_to_text
from .timeutil import parse_utc, to_utc_str

REGISTRY = ROOT / "registry" / "expectations.json"
META_TIME_RES = [
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"'),
    re.compile(r'property="article:published_time"\s+content="([^"]+)"'),
    re.compile(r'content="([^"]+)"\s+property="article:published_time"'),
    re.compile(r'name="(?:date|publish-date|sailthru.date)"\s+content="([^"]+)"'),
]


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


MODIFIED_RES = [
    re.compile(r'"dateModified"\s*:\s*"([^"]+)"'),
    re.compile(r'property="article:modified_time"\s+content="([^"]+)"'),
    re.compile(r'content="([^"]+)"\s+property="article:modified_time"'),
]


def _first_time(body: bytes, regexes):
    html = body.decode("utf-8", "replace")
    for rx in regexes:
        m = rx.search(html)
        if m:
            try:
                return to_utc_str(parse_utc(m.group(1).replace("+0000", "+00:00")))
            except Exception:
                continue
    return None


def page_published_at(body: bytes):
    return _first_time(body, META_TIME_RES)


def page_modified_at(body: bytes):
    return _first_time(body, MODIFIED_RES)


def verify_quote(body: bytes, quote: str) -> int:
    """1 = found in the rendered page text; 2 = found only in raw markup/scripts (e.g. article bodies that
    are delivered as script data); 0 = not found."""
    import html as htmllib
    q = normalise(quote)
    text = html_to_text(body) if b"<" in body[:2000] else body.decode("utf-8", "replace")
    if q in normalise(text):
        return 1
    raw = body.decode("utf-8", "replace").replace("\\u002F", "/").replace('\\"', '"')
    raw = htmllib.unescape(re.sub(r"<[^>]+>", " ", raw))
    return 2 if q in normalise(raw) else 0


def grade(source_kind, quote_ok, published_at, event_at, modified_at=None):
    """modified_at: if the page content was last modified after the event, the quoted text may have been
    written after the event (e.g. 'results vs expectations' pages created before and rewritten after)."""
    if not quote_ok or not published_at:
        return "D"
    before = published_at <= event_at and (modified_at is None or modified_at <= event_at)
    if source_kind == "sec_edgar":
        return "A" if before else "C"
    return "B" if before else "C"


def load_registry(path: Path = REGISTRY):
    return json.loads(Path(path).read_text())


def ingest_registry(conn, store, run_id, stats, path: Path = REGISTRY) -> int:
    items = load_registry(path)
    n = 0
    for it in items:
        comp = conn.execute("SELECT * FROM company WHERE ticker=?", (it["ticker"],)).fetchone()
        event_at = it["event_public_at"]
        doc_id, body, quote_ok, retrieved_at, err = None, None, False, None, None
        prev = store.latest(it["source_url"])
        try:
            if prev is not None and prev["http_status"] == 200:
                doc_id, retrieved_at = prev["doc_id"], prev["retrieved_at"]
                body = store.read(doc_id)
            else:
                f = store.fetch(it["source_url"], source_system=it["source_kind"] if it["source_kind"] != "sec_edgar"
                                else "sec_edgar", doc_type="expectation_source", run_id=run_id,
                                company_id=comp["company_id"], title=it.get("title"))
                doc_id, body, retrieved_at = f.doc_id, f.body, f.retrieved_at
        except FetchError as e:
            err = str(e)
        if body is not None and it.get("verbatim_quote"):
            quote_ok = verify_quote(body, it["verbatim_quote"])

        stated = it.get("source_published_at")
        basis = it.get("published_at_basis", "as stated in registry")
        meta = page_published_at(body) if body is not None else None
        modified = page_modified_at(body) if body is not None and it["source_kind"] != "sec_edgar" else None
        if it.get("source_modified_at"):
            modified = max(filter(None, [modified, it["source_modified_at"]]))
        notes = [it.get("notes") or ""]
        if it["source_kind"] == "sec_edgar":
            acc = it.get("accession_no")
            r = conn.execute("SELECT acceptance_datetime FROM filing WHERE accession_no=?", (acc,)).fetchone()
            if r:
                stated, basis = r["acceptance_datetime"], "sec_acceptance_datetime"
            else:
                notes.append(f"accession {acc} not in filing index")
        elif meta:
            if stated and abs((parse_utc(meta) - parse_utc(stated)).total_seconds()) > 36 * 3600:
                notes.append(f"registry date {stated} disagrees with page metadata {meta}; page metadata used")
            if not stated or abs((parse_utc(meta) - parse_utc(stated)).total_seconds()) > 36 * 3600:
                stated, basis = meta, "page_metadata_published_time"
            else:
                basis += "; consistent with page metadata"
        if err:
            notes.append(f"fetch failed: {err}")
        g = grade(it["source_kind"], quote_ok, stated, event_at, modified)
        if modified and stated and stated <= event_at < modified:
            notes.append(f"page first published before the event but last modified {modified}, after it: "
                         "content treated as post-event")
        avail = stated if g in ("A", "B") else None
        nk = f"exp|{it['event_key']}|{it['expectation_type']}|{it['metric']}|{it['provider']}|{it['source_url']}"
        cur = conn.execute(
            """INSERT OR IGNORE INTO expectation(company_id, event_key, expectation_type, metric, period_end, value_low,
                 value_high, value_point, unit, provider, reported_by, source_doc_id, source_url, source_published_at,
                 published_at_basis, verbatim_quote, quote_verified, provenance_grade, available_at, notes,
                 retrieved_at, natural_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (comp["company_id"], it["event_key"], it["expectation_type"], it["metric"], it.get("period_end"),
             it.get("value_low"), it.get("value_high"), it.get("value_point"), it.get("unit"), it["provider"],
             it.get("reported_by"), doc_id, it["source_url"], stated, basis, it.get("verbatim_quote"),
             quote_ok, g, avail, "; ".join(x for x in notes if x) or None, retrieved_at or "", nk, run_id))
        n += cur.rowcount
    stats["expectations_new"] = n
    return n


def event_key_for(conn, ev) -> str | None:
    """TICKER:FY{year}Q{q} from release observations of the same accession, else from the report date."""
    r = conn.execute("""SELECT fiscal_year, fiscal_period FROM observation WHERE accession_no=?
                        AND source_kind='earnings_release_table' LIMIT 1""", (ev["accession_no"],)).fetchone()
    tk = conn.execute("SELECT ticker, fiscal_year_end FROM company WHERE company_id=?", (ev["company_id"],)).fetchone()
    if r:
        return f"{tk['ticker']}:FY{r['fiscal_year']}{r['fiscal_period']}"
    from datetime import date, timedelta
    from .fiscal import fiscal_label
    from .timeutil import ET, parse_utc
    d = parse_utc(ev["available_at"]).astimezone(ET).date()      # public date, not the 8-K report_date
    # last calendar quarter end strictly before the announcement
    qe = [date(d.year - 1, 12, 31), date(d.year, 3, 31), date(d.year, 6, 30), date(d.year, 9, 30)]
    pe = max(x for x in qe if x < d - timedelta(days=5))
    fye = tk["fiscal_year_end"] if tk["fiscal_year_end"] not in (None, "0000") else "1231"   # unknown FYE
    fy, fp = fiscal_label(date(pe.year, pe.month, 1) - timedelta(days=60), pe, fye)
    return f"{tk['ticker']}:FY{fy}{fp}"


def _attach_actual(item, act):
    lo, hi, pt = item.get("low"), item.get("high"), item.get("point")
    ref = pt if pt is not None else ((lo + hi) / 2 if lo is not None and hi is not None else None)
    if ref is None or act is None:
        return
    item["actual"] = act
    item["surprise_vs_reference"] = (act - ref) if item.get("unit") == "percent" else act / ref - 1
    item["surprise_unit"] = "percentage points" if item.get("unit") == "percent" else "relative"
    if lo is not None and hi is not None:
        item["actual_vs_range"] = "above" if act > hi else "below" if act < lo else "within"


def expectations_for_event(conn, ev, public_at, knowledge_time) -> dict:
    key = event_key_for(conn, ev)
    rows = conn.execute("""SELECT * FROM expectation e WHERE event_key=? AND retrieved_at<=? AND expectation_id = (
                             SELECT max(expectation_id) FROM expectation e2 WHERE e2.natural_key=e.natural_key
                             AND e2.retrieved_at<=?)
                           ORDER BY expectation_type, metric, provenance_grade""",
                        (key, knowledge_time, knowledge_time)).fetchall()
    actuals = {r["concept"]: r["value"] for r in conn.execute(
        """SELECT concept, value FROM observation WHERE accession_no=? AND source_kind='earnings_release_table'
           AND available_at<=?""", (ev["accession_no"], public_at))}
    usable, descriptive, rejected, kpis, documented, issued = [], [], [], [], [], []
    for r in rows:
        if r["expectation_type"] == "documented_reaction":
            documented.append({"metric": r["metric"], "point": r["value_point"], "unit": r["unit"],
                               "reported_by": r["provider"], "quote": r["verbatim_quote"], "source_url": r["source_url"],
                               "source_published_at": r["source_published_at"], "notes": r["notes"],
                               "evidence": {"table": "expectation", "id": r["expectation_id"]},
                               "label": "documented by a dated news source (not measured by this system)"})
            continue
        if r["expectation_type"] == "guidance_issued":
            if r["quote_verified"]:
                issued.append({"metric": r["metric"], "low": r["value_low"], "high": r["value_high"],
                               "unit": r["unit"], "quote": r["verbatim_quote"], "source_url": r["source_url"],
                               "public_at": r["source_published_at"], "basis": r["published_at_basis"],
                               "notes": r["notes"], "evidence": {"table": "expectation", "id": r["expectation_id"]}})
            continue
        if r["expectation_type"] == "reported_kpi":
            if r["available_at"] and r["available_at"] <= public_at:
                actuals.setdefault(r["metric"], r["value_point"])
                kpis.append({"metric": r["metric"], "value": r["value_point"], "unit": r["unit"],
                             "quote": r["verbatim_quote"], "source_url": r["source_url"],
                             "evidence": {"table": "expectation", "id": r["expectation_id"]}})
            continue
        item = {"metric": r["metric"], "type": r["expectation_type"], "provider": r["provider"],
                "reported_by": r["reported_by"], "low": r["value_low"], "high": r["value_high"],
                "point": r["value_point"], "unit": r["unit"], "grade": r["provenance_grade"],
                "source_published_at": r["source_published_at"], "basis": r["published_at_basis"],
                "source_url": r["source_url"],
                "quote_verified": {0: "no", 1: "rendered_text", 2: "raw_source_only"}[r["quote_verified"]],
                "evidence": {"table": "expectation", "id": r["expectation_id"]}}
        if r["available_at"] and r["available_at"] <= public_at:
            usable.append(item)
        elif r["provenance_grade"] == "C":
            item["label"] = "documented after the event: descriptive only, not point-in-time"
            descriptive.append(item)
        else:
            item["label"] = "rejected (quote unverified or undated)" if r["provenance_grade"] == "D" else \
                "not available before the event"
            rejected.append(item)
    for lst in (usable, descriptive, rejected):
        for item in lst:
            if "actual" not in item and item["metric"] in actuals:
                _attach_actual(item, actuals[item["metric"]])
    return {"event_key": key, "point_in_time": usable, "post_event_documented": descriptive,
            "rejected": rejected, "reported_kpis": kpis, "documented_reactions": documented,
            "guidance_issued_with_event": issued,
            "note": None if rows else "no curated expectations for this event (none fabricated)"}
