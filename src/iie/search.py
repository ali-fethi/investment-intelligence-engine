"""Evidence retrieval over indexed filing text, with citations and an optional point-in-time filter."""
from __future__ import annotations

import re


STOP = {"the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "by", "with", "is", "are", "what", "how"}


def _fts_query(q: str) -> str:
    """Quoted tokens (punctuation cannot break FTS5 syntax) joined with OR; BM25 ranks documents that
    match more terms higher. A phrase in double quotes is kept as a phrase."""
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    toks = [t for t in re.findall(r"[\w'.-]+", rest) if t.lower() not in STOP]
    parts = ['"' + p.replace('"', "") + '"' for p in phrases] + ['"' + t + '"' for t in toks]
    return " OR ".join(parts) if parts else '""'


def search(conn, query: str, ticker: str | None = None, as_of: str | None = None, limit: int = 8):
    sql = """SELECT c.chunk_id, c.doc_id, c.section, c.char_start, c.char_end, c.text,
                    snippet(chunk_fts, 0, '[', ']', ' … ', 24) AS snip, bm25(chunk_fts) AS score,
                    d.url, d.title, d.sha256, d.doc_type, d.accession_no, d.retrieved_at,
                    f.acceptance_datetime, f.form, f.report_date, co.ticker
             FROM chunk_fts JOIN document_chunk c ON c.chunk_id = chunk_fts.rowid
             JOIN source_document d ON d.doc_id = c.doc_id
             LEFT JOIN filing f ON f.accession_no = d.accession_no
             LEFT JOIN company co ON co.company_id = d.company_id
             WHERE chunk_fts MATCH ?"""
    params = [_fts_query(query)]
    if ticker:
        sql += " AND co.ticker = ?"
        params.append(ticker.upper())
    if as_of:
        sql += " AND f.acceptance_datetime <= ?"
        params.append(as_of)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    out = []
    for r in conn.execute(sql, params).fetchall():
        out.append({
            "ticker": r["ticker"], "form": r["form"], "doc_type": r["doc_type"], "report_date": r["report_date"],
            "section": r["section"], "snippet": r["snip"].replace("\n", " "), "score": round(r["score"], 3),
            "citation": {"url": r["url"], "accession_no": r["accession_no"], "doc_id": r["doc_id"],
                         "chunk_id": r["chunk_id"], "char_range": [r["char_start"], r["char_end"]],
                         "sha256": r["sha256"], "public_at": r["acceptance_datetime"],
                         "retrieved_at": r["retrieved_at"]},
        })
    return out


def format_citation(hit) -> str:
    c = hit["citation"]
    return (f"{hit['ticker']} {hit['form']} ({hit['doc_type']}, period {hit['report_date']}), "
            f"{hit['section'] or 'no section'} — {c['url']} chars {c['char_range'][0]}-{c['char_range'][1]}; "
            f"public {c['public_at']}; sha256 {c['sha256'][:12]}…; doc#{c['doc_id']} chunk#{c['chunk_id']}")
