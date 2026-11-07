"""Management guidance, extracted from primary issuer sources with the quote and its location.

* Meta: 'CFO Outlook Commentary' in the 8-K Item 2.02 exhibit (EDGAR acceptance => grade A).
  Revisions ('updated from our prior outlook of ...') are recorded as new observations of the same target.
* Microsoft: outlook given by the CFO on the earnings call (IR transcript page; the call date is printed on the
  page, the 5:30 pm ET start is Microsoft's standard and is assumed => grade B). Target = next fiscal quarter.
* Alphabet: no quantitative guidance. Each release is scanned; absence is recorded per quarter (grade A),
  never filled with a number.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..evidence import FetchError
from ..ingest.documents import html_to_text, ingest_filing_documents
from ..timeutil import et_to_utc_str
from .store import put

QUARTERS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
META_RE = re.compile(
    r"[Ww]e (?:expect|anticipate)\s+(?:our\s+)?(?P<period>(?:first|second|third|fourth) quarter (?:of )?\d{4}|"
    r"(?:full[- ]year )?\d{4})\s+(?P<metric>total revenue|total expenses|capital expenditures)"
    r"(?P<mid>[^.$]{0,120}?)(?:will be |to be )?in the range of \$(?P<lo>[\d.]+)(?:\s*billion)?\s*(?:-|–|to)\s*"
    r"\$?(?P<hi>[\d.]+)\s*billion(?P<tail>[^.]{0,160})")
META_ALT_RE = re.compile(
    r"(?:For (?P<y>\d{4}), )?[Ww]e (?:expect|anticipate)\s+(?:our\s+)?(?:full[- ]year\s+)?"
    r"(?P<metric>total revenue|total expenses|capital expenditures)(?P<mid>[^.$]{0,120}?)(?:will be |to be )?in the range of "
    r"\$(?P<lo>[\d.]+)(?:\s*billion)?\s*(?:-|–|to)\s*\$?(?P<hi>[\d.]+)\s*billion(?P<tail>[^.]{0,160})")
META_2020_RE = re.compile(
    r"[Ww]e expect (?P<metric>total revenue|total expenses|capital expenditures) in (?P<y>\d{4})(?P<mid>[^.$]{0,80}?) "
    r"to be (?:in the range of|between) \$(?P<lo>[\d.]+)(?:\s*billion)?\s*(?:-|–|to|and)\s*\$?(?P<hi>[\d.]+)\s*billion"
    r"(?P<tail>[^.]{0,160})")
PRIOR_RE = re.compile(r"(?:updated|narrowed|raised|lowered|increased|reduced|down|up)[^.]{0,40}?(?:from )?(?:our |the )?prior "
                      r"(?:outlook|range|estimate) of \$([\d.]+)\s*(?:-|–|to)\s*\$?([\d.]+) billion")
MS_SEGMENTS = {"productivity and business processes": "segment_revenue_pbp", "intelligent cloud":
               "segment_revenue_intelligent_cloud", "more personal computing": "segment_revenue_mpc"}
MS_RANGE = re.compile(r"\$\s?([\d.]+)\s*(?:billion\s*)?to\s*\$\s?([\d.]+)\s*billion")
MS_AZURE = re.compile(r"In Azure, we expect[^.]*?(\d+)%\s*to\s*(\d+)%\s*in constant currency")
MS_AZURE_TEXT = re.compile(r"In Azure, we expect[^.]*\.")
MS_URL = "https://www.microsoft.com/en-us/investor/events/fy-{fy}/earnings-fy-{fy}-q{q}"
MS_DATE = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday),? (January|February|March|April|May|June|July|"
                     r"August|September|October|November|December) (\d{1,2}),? (\d{4})")


def _target(period: str) -> str:
    m = re.match(r"(first|second|third|fourth) quarter (?:of )?(\d{4})", period)
    if m:
        return f"FY{m.group(2)}Q{QUARTERS[m.group(1)]}"
    year = re.search(r"(\d{4})", period).group(1)
    return f"FY{year}"


def parse_meta_outlook(text: str):
    t = re.sub(r"\s+", " ", text)
    out = []
    for m in META_RE.finditer(t):
        metric = {"total revenue": "revenue", "total expenses": "total_expenses",
                  "capital expenditures": "capex"}[m.group("metric")]
        item = {"target": _target(m.group("period")), "metric": metric, "low": round(float(m.group("lo")) * 1e9),
                "high": round(float(m.group("hi")) * 1e9), "quote": m.group(0)[:400], "offset": m.start()}
        p = PRIOR_RE.search(m.group("tail") or "")
        if p:
            item["prior_low"], item["prior_high"] = float(p.group(1)) * 1e9, float(p.group(2)) * 1e9
        out.append(item)
    seen = {(o["target"], o["metric"]) for o in out}
    for m in META_2020_RE.finditer(t):
        metric = {"total revenue": "revenue", "total expenses": "total_expenses",
                  "capital expenditures": "capex"}[m.group("metric")]
        key = (f"FY{m.group('y')}", metric)
        if key in seen:
            continue
        item = {"target": key[0], "metric": metric, "low": round(float(m.group("lo")) * 1e9),
                "high": round(float(m.group("hi")) * 1e9), "quote": m.group(0)[:400], "offset": m.start()}
        p = PRIOR_RE.search(m.group("tail") or "") or re.search(
            r"(?:down|up) from the prior range of \$([\d.]+)\s*(?:-|–|to)\s*\$?([\d.]+) billion", m.group("tail") or "")
        if p:
            item["prior_low"], item["prior_high"] = float(p.group(1)) * 1e9, float(p.group(2)) * 1e9
        out.append(item)
        seen.add(key)
    for m in META_ALT_RE.finditer(t):
        if any(o["offset"] == m.start() or abs(o["offset"] - m.start()) < 3 for o in out):
            continue
        metric = {"total revenue": "revenue", "total expenses": "total_expenses",
                  "capital expenditures": "capex"}[m.group("metric")]
        if m.group("y"):
            year, how = m.group("y"), "stated"
        else:
            # implicit year: the latest outlook target stated explicitly EARLIER in the same release (not any year
            # mentioned nearby, which may refer to the quarter just reported)
            earlier = [o for o in out if o["offset"] < m.start() and o.get("year_basis", "stated") == "stated"]
            if not earlier:
                continue
            year = max(int(o["target"][2:6]) for o in earlier)
            year, how = str(year), "inferred from an explicit outlook target earlier in the release"
        key = (f"FY{year}", metric)
        if key in seen:
            continue
        item = {"target": key[0], "metric": metric, "low": round(float(m.group("lo")) * 1e9),
                "high": round(float(m.group("hi")) * 1e9), "quote": m.group(0)[:400], "offset": m.start(),
                "year_basis": how}
        p = PRIOR_RE.search(m.group("tail") or "")
        if p:
            item["prior_low"], item["prior_high"] = float(p.group(1)) * 1e9, float(p.group(2)) * 1e9
        out.append(item)
        seen.add(key)
    return out


def fetch_release_exhibits(conn, store, company, since, run_id, stats):
    """Earnings-release exhibits only (8-K Item 2.02), for the guidance history before the full-text window."""
    have_until = conn.execute("""SELECT min(f.filing_date) FROM source_document d JOIN filing f
                                 ON f.accession_no=d.accession_no WHERE d.company_id=? AND d.doc_type LIKE 'EX-99%'""",
                              (company["company_id"],)).fetchone()[0] or "9999-12-31"
    from ..ingest import documents
    orig = documents.select_filings
    documents.select_filings = lambda c, cid, s, u=None: [r for r in orig(c, cid, s, u) if r["form"] == "8-K"]
    try:
        ingest_filing_documents(conn, store, company, since, run_id, stats, until=have_until)
    finally:
        documents.select_filings = orig


def ingest_meta(conn, store, company, run_id, stats) -> int:
    n = 0
    rows = conn.execute("""SELECT d.doc_id, d.url, d.accession_no, f.acceptance_datetime FROM source_document d
                           JOIN filing f ON f.accession_no=d.accession_no WHERE d.company_id=? AND d.doc_type='EX-99.1'
                           AND f.form='8-K' AND f.items LIKE '%2.02%' ORDER BY f.acceptance_datetime""",
                        (company["company_id"],)).fetchall()
    for r in rows:
        items = parse_meta_outlook(html_to_text(store.read(r["doc_id"])))
        stats["meta_releases_scanned"] = stats.get("meta_releases_scanned", 0) + 1
        if items:
            stats["meta_releases_with_outlook"] = stats.get("meta_releases_with_outlook", 0) + 1
        for it in items:
            for stat in ("low", "high"):
                n += put(conn, company_id=company["company_id"], cls="management_guidance", provider="Meta management",
                         metric=it["metric"], basis="GAAP, as guided (CFO outlook)", target_period=it["target"],
                         stat=stat, value=it[stat], unit="USD", as_of=r["acceptance_datetime"],
                         as_of_basis="sec_acceptance_datetime", available_at=r["acceptance_datetime"], grade="A",
                         source_doc_id=r["doc_id"], source_url=r["url"], locator=it["quote"],
                         method="meta_outlook_regex_v2",
                         notes="; ".join(x for x in (
                             f"revision: prior outlook {it['prior_low'] / 1e9:g}-{it['prior_high'] / 1e9:g}bn"
                             if "prior_low" in it else None,
                             f"target year {it['year_basis']}" if it.get("year_basis", "stated") != "stated" else None)
                             if x) or None,
                         natural_key=f"metaguid|{r['accession_no']}|{it['metric']}|{it['target']}|{stat}", run_id=run_id)
    return n


def parse_ms_transcript(text: str):
    """Segment revenue ranges and Azure guidance from the CFO's outlook remarks."""
    t = re.sub(r"\s+", " ", text)
    start = max(t.find("outlook"), t.find("Outlook"), 0)
    body = t[start:]
    sentences = re.split(r"(?<=[.!?])\s+", body)
    seg, out = None, []
    for s in sentences:
        low = s.lower()
        for name, metric in MS_SEGMENTS.items():
            if name in low:
                seg = metric
        if "we expect" in low and "revenue" in low and seg:
            m = MS_RANGE.search(s)
            if m and not any(o["metric"] == seg for o in out):
                out.append({"metric": seg, "low": round(float(m.group(1)) * 1e9), "high": round(float(m.group(2)) * 1e9),
                            "quote": s[:400]})
        if s.startswith("In Azure, we expect") and not any(o["metric"] == "azure_growth_cc" for o in out):
            m = MS_AZURE.search(s)
            if m:
                out.append({"metric": "azure_growth_cc", "low": float(m.group(1)), "high": float(m.group(2)),
                            "unit": "percent", "quote": s[:400]})
            else:
                out.append({"metric": "azure_growth_cc", "text": s[:400], "unit": "percent", "quote": s[:400]})
    return out


def ingest_msft(conn, store, company, run_id, stats, fy_range=range(2022, 2028)) -> int:
    n = 0
    for fy in fy_range:
        for q in (1, 2, 3, 4):
            url = MS_URL.format(fy=fy, q=q)
            prev = store.latest(url)
            try:
                if prev is not None and prev["http_status"] == 200:
                    doc_id, body = prev["doc_id"], store.read(prev["doc_id"])
                else:
                    f = store.fetch(url, source_system="issuer_ir", doc_type="earnings_call_transcript", run_id=run_id,
                                    company_id=company["company_id"], title=f"Microsoft FY{fy} Q{q} earnings call")
                    doc_id, body = f.doc_id, f.body
            except FetchError:
                stats.setdefault("msft_transcripts_unavailable", []).append(f"FY{fy}Q{q}")
                continue
            text = html_to_text(body)
            dm = MS_DATE.search(text)
            if not dm:
                stats.setdefault("msft_transcripts_undated", []).append(url)
                continue
            call_day = datetime.strptime(f"{dm.group(2)} {dm.group(3)} {dm.group(4)}", "%B %d %Y").date()
            as_of = et_to_utc_str(call_day, 17, 30)
            target = f"FY{fy + 1}Q1" if q == 4 else f"FY{fy}Q{q + 1}"
            items = parse_ms_transcript(text)
            stats["msft_transcripts_parsed"] = stats.get("msft_transcripts_parsed", 0) + 1
            for it in items:
                for stat in (("low", "high") if "low" in it else ("text",)):
                    n += put(conn, company_id=company["company_id"], cls="management_guidance",
                             provider="Microsoft management", metric=it["metric"],
                             basis="constant currency" if it["metric"] == "azure_growth_cc" else
                             "USD, segment definitions in force at the call", target_period=target, stat=stat,
                             value=it.get(stat) if stat != "text" else None,
                             value_text=it.get("text") if stat == "text" else None, unit=it.get("unit", "USD"),
                             as_of=as_of, as_of_basis="issuer transcript page: call date printed on page; 17:30 ET "
                             "call start assumed", available_at=as_of, grade="B", source_doc_id=doc_id,
                             source_url=url, locator=it["quote"], method="msft_transcript_parser_v1",
                             natural_key=f"msguid|FY{fy}Q{q}|{it['metric']}|{stat}", run_id=run_id)
    return n


GOOG_CAPEX = [
    re.compile(r"we (?:now )?expect to invest approximately \$(?P<pt>[\d.]+) billion in capital expenditures in (?P<y>\d{4})", re.I),
    re.compile(r"we (?:now )?expect (?P<y>\d{4}) capital expenditures to be in a range of \$(?P<lo>[\d.]+) billion to "
               r"\$(?P<hi>[\d.]+) billion", re.I),
    re.compile(r"our (?P<y>\d{4}) CapEx investments are anticipated to be in the range of \$(?P<lo>[\d.]+) to "
               r"\$(?P<hi>[\d.]+) billion", re.I),
]


def parse_alphabet_capex(text: str):
    t = re.sub(r"\s+", " ", text)
    out = []
    for rx in GOOG_CAPEX:
        for m in rx.finditer(t):
            g = m.groupdict()
            item = {"target": f"FY{g['y']}", "quote": m.group(0)}
            if g.get("pt"):
                item["point"] = round(float(g["pt"]) * 1e9)
            else:
                item["low"], item["high"] = round(float(g["lo"]) * 1e9), round(float(g["hi"]) * 1e9)
            out.append(item)
    return out


NO_GUIDANCE_RE = re.compile(r"\b(?:we expect|we anticipate|outlook for|guidance for|we are guiding)\b[^.]{0,80}"
                            r"\b(?:revenue|revenues|expenses|operating margin|capital expenditures)\b", re.I)


def ingest_alphabet(conn, store, company, run_id, stats) -> int:
    n = 0
    rows = conn.execute("""SELECT d.doc_id, d.url, d.accession_no, f.acceptance_datetime FROM source_document d
                           JOIN filing f ON f.accession_no=d.accession_no WHERE d.company_id=? AND d.doc_type='EX-99.1'
                           AND f.form='8-K' AND f.items LIKE '%2.02%' ORDER BY f.acceptance_datetime""",
                        (company["company_id"],)).fetchall()
    from ..expectations import event_key_for
    for r in rows:
        text = re.sub(r"\s+", " ", html_to_text(store.read(r["doc_id"])))
        hits = [m.group(0) for m in NO_GUIDANCE_RE.finditer(text)]
        capex = parse_alphabet_capex(text)
        for it in capex:
            for stat in [k for k in ("point", "low", "high") if k in it]:
                n += put(conn, company_id=company["company_id"], cls="management_guidance",
                         provider="Alphabet management", metric="capex", basis="as guided in the release",
                         target_period=it["target"], stat=stat, value=it[stat], unit="USD", as_of=r["acceptance_datetime"],
                         as_of_basis="sec_acceptance_datetime", available_at=r["acceptance_datetime"], grade="A",
                         source_doc_id=r["doc_id"], source_url=r["url"], locator=it["quote"],
                         method="alphabet_capex_regex_v1",
                         natural_key=f"googcapex|{r['accession_no']}|{it['target']}|{stat}", run_id=run_id)
        ev = conn.execute("SELECT * FROM event WHERE accession_no=?", (r["accession_no"],)).fetchone()
        key = event_key_for(conn, ev).split(":")[1]
        fy, q = int(key[2:6]), int(key[-1])
        target = f"FY{fy + 1}Q1" if q == 4 else f"FY{fy}Q{q + 1}"
        n += put(conn, company_id=company["company_id"], cls="management_guidance", provider="Alphabet management",
                 metric="quantitative_guidance", target_period=target, stat="text",
                 value_text=("none in release" if not hits and not capex else
                             "no revenue/earnings guidance; capex guidance only" if capex else
                             "possible forward-looking statement: review"),
                 as_of=r["acceptance_datetime"], as_of_basis="sec_acceptance_datetime",
                 available_at=r["acceptance_datetime"], grade="A", source_doc_id=r["doc_id"], source_url=r["url"],
                 locator="; ".join(hits[:3]) or "no outlook/guidance sentence found by scan",
                 method="alphabet_no_guidance_scan_v2", checks={"forward_looking_hits": hits[:5],
                                                                "capex_guidance": [c["quote"] for c in capex]},
                 notes="absence of guidance is an observation about the release, not a forecast",
                 natural_key=f"goognoguid|{r['accession_no']}", run_id=run_id)
    return n


def ingest(conn, store, company, run_id, stats) -> int:
    tk = company["ticker"]
    if tk in ("META", "GOOGL"):
        fetch_release_exhibits(conn, store, company, "2016-01-01", run_id, stats)
        from ..ingest.earnings_release import ingest_releases
        ingest_releases(conn, company, run_id, stats, store)
    if tk == "META":
        return ingest_meta(conn, store, company, run_id, stats)
    if tk == "MSFT":
        return ingest_msft(conn, store, company, run_id, stats)
    if tk == "GOOGL":
        return ingest_alphabet(conn, store, company, run_id, stats)
    return 0
