"""Filing documents (10-K, 10-Q, 8-K earnings-release exhibits): download, store, extract text, index.

For each selected filing we also store the EDGAR index-headers page, whose ACCEPTANCE-DATETIME
(Eastern time) independently confirms the acceptance timestamp from the submissions API.
"""
from __future__ import annotations

import html as htmllib
import re

from bs4 import BeautifulSoup

from ..evidence import EvidenceStore, FetchError
from ..timeutil import edgar_header_acceptance_to_utc
from .sec import PERIODIC_FORMS, archive_url

HEADER_DOC_RE = re.compile(r"<DOCUMENT>\s*<TYPE>([^\s<]+).*?<FILENAME>([^\s<]+)", re.S | re.I)
ACCEPT_RE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})")
SECTION_RE = re.compile(r"^(PART\s+[IV]+\b.*|Item\s+\d+[A-C]?\..{0,80})$", re.I)


def select_filings(conn, company_id, since: str, until: str | None = None):
    rows = conn.execute(
        """SELECT * FROM filing WHERE company_id=? AND filing_date>=? AND filing_date<=?
           AND (form IN ('10-K','10-Q','10-K/A','10-Q/A') OR (form='8-K' AND items LIKE '%2.02%'))
           ORDER BY acceptance_datetime""", (company_id, since, until or "9999-12-31")).fetchall()
    return rows


def parse_index_headers(text: str):
    t = htmllib.unescape(text)
    m = ACCEPT_RE.search(t)
    docs = [(typ.upper(), fn) for typ, fn in HEADER_DOC_RE.findall(t)]
    return (edgar_header_acceptance_to_utc(m.group(1)) if m else None), docs


def ingest_filing_documents(conn, store: EvidenceStore, company, since, run_id, stats, until=None) -> int:
    n = 0
    for fl in select_filings(conn, company["company_id"], since, until):
        acc = fl["accession_no"]
        have = conn.execute("SELECT count(*) FROM source_document WHERE accession_no=? AND doc_type NOT IN "
                            "('index_headers')", (acc,)).fetchone()[0]
        if have:
            continue
        hdr_url = archive_url(company["cik"], acc, f"{acc}-index-headers.html")
        h = store.fetch(hdr_url, source_system="sec_edgar", doc_type="index_headers", run_id=run_id,
                        company_id=company["company_id"], accession_no=acc, title=f"{fl['form']} {acc} headers")
        header_accept, docs = parse_index_headers(h.body.decode("utf-8", "replace"))
        wanted = []
        if fl["form"] in PERIODIC_FORMS:
            wanted = [(fl["form"], fl["primary_document"])]
        else:
            wanted = [(t, fn) for t, fn in docs if t.startswith("EX-99")]
        for typ, fn in wanted:
            if not fn:
                continue
            try:
                d = store.fetch(archive_url(company["cik"], acc, fn), source_system="sec_edgar", doc_type=typ,
                                run_id=run_id, company_id=company["company_id"], accession_no=acc,
                                title=f"{company['ticker']} {fl['form']} {typ} {fl['report_date'] or ''} ({acc})",
                                published_at=header_accept or fl["acceptance_datetime"],
                                published_at_basis="edgar_header_acceptance_datetime" if header_accept
                                else "submissions_api_acceptance_datetime")
            except FetchError as e:
                stats.setdefault("doc_errors", []).append(str(e))
                continue
            index_document(conn, d.doc_id, d.body)
            n += 1
        conn.commit()
    stats[f"documents_new_{company['ticker']}"] = n
    return n


def html_to_text(body: bytes) -> str:
    soup = BeautifulSoup(body, "lxml")
    for tag in soup(["script", "style", "ix:header", "head"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        tag.decompose()
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for blk in soup.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"]):
        blk.append("\n")
    text = soup.get_text(" ")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n[ \n]*", "\n", text)
    return text.strip()


def _lines_with_offsets(text: str, max_len: int):
    pos = 0
    for line in text.split("\n"):
        start = pos
        pos += len(line) + 1
        while len(line) > max_len:
            cut = line.rfind(" ", 0, max_len)
            cut = cut if cut > max_len // 2 else max_len
            yield start, start + cut, line[:cut]
            start, line = start + cut, line[cut:]
        yield start, start + len(line), line


def chunk_text(text: str, target: int = 1200, overlap: int = 150):
    """Chunks of ~target chars on line boundaries, with absolute character offsets into `text`.
    section = heading in effect at the chunk start, followed by headings that open inside the chunk."""
    chunks = []
    current_heading = None
    cur = None  # [start, end, headings]
    for s, e, line in _lines_with_offsets(text, target):
        is_heading = bool(SECTION_RE.match(line.strip()))
        if cur is None:
            cur = [s, e, [current_heading] if current_heading else []]
        if is_heading:
            current_heading = line.strip()[:120]
            if current_heading not in cur[2]:
                cur[2].append(current_heading)
        cur[1] = e
        if cur[1] - cur[0] >= target:
            chunks.append(tuple(cur))
            nxt = max(cur[1] - overlap, cur[0] + 1)
            sp = text.find(" ", nxt, cur[1])
            nxt = sp + 1 if sp >= 0 else cur[1]
            cur = [nxt, cur[1], [current_heading] if current_heading else []] if nxt < cur[1] else None
    if cur is not None and cur[1] > cur[0]:
        chunks.append(tuple(cur))
    return [(s, e, " | ".join(h)[:300] if h else None, text[s:e]) for s, e, h in chunks]


def index_document(conn, doc_id: int, body: bytes) -> int:
    if conn.execute("SELECT 1 FROM document_chunk WHERE doc_id=? LIMIT 1", (doc_id,)).fetchone():
        return 0
    text = html_to_text(body)
    n = 0
    for seq, (s, e, sec, t) in enumerate(chunk_text(text)):
        cur = conn.execute("INSERT INTO document_chunk(doc_id, seq, section, char_start, char_end, text) "
                           "VALUES (?,?,?,?,?,?)", (doc_id, seq, sec, s, e, t))
        conn.execute("INSERT INTO chunk_fts(rowid, text) VALUES (?,?)", (cur.lastrowid, t))
        n += 1
    return n
