"""Internet Archive (Wayback Machine) captures as dated evidence.

A capture's timestamp is set by the archive, independently of the publisher, so a page captured at T
proves the content was public no later than T. Captures are fetched raw (`id_`), stored in the evidence
store, and `published_at` is the capture time with basis `wayback_capture_timestamp`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..evidence import FetchError
from ..timeutil import to_utc_str

CDX = ("http://web.archive.org/cdx/search/cdx?url={url}&from={start}&to={end}&filter=statuscode:200"
       "&fl=timestamp,original,digest")
RAW = "http://web.archive.org/web/{ts}id_/{original}"


def ts_to_utc(ts: str) -> str:
    return to_utc_str(datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc))


def list_captures(store, url: str, start="2016", end="2026", run_id=None):
    """CDX index for a URL; the index response itself is stored as evidence."""
    f = store.fetch(CDX.format(url=url, start=start, end=end), source_system="wayback", doc_type="cdx_index",
                    run_id=run_id, title=f"Wayback CDX {url}")
    out, seen = [], set()
    for line in f.body.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] not in seen:      # identical digests = identical content
            seen.add(parts[2])
            out.append({"timestamp": parts[0], "original": parts[1], "captured_at": ts_to_utc(parts[0])})
    return out


def served_timestamp(final_url: str, requested: str) -> str:
    """The archive may redirect a capture request to a neighbouring capture: return the one actually served."""
    import re
    m = re.search(r"/web/(\d{14})", final_url or "")
    return m.group(1) if m else requested


def served_check(store, raw_url):
    """Which capture the archive serves for a raw capture URL, from a stored check (evidence) or a new HEAD request.
    Returns the 14-digit timestamp, or None if it cannot be established."""
    import json as _json
    prev = store.conn.execute("""SELECT doc_id FROM source_document WHERE doc_type='wayback_served_check' AND url=?
                                 ORDER BY doc_id DESC LIMIT 1""", (raw_url + "#served",)).fetchone()
    if prev:
        return _json.loads(store.read(prev["doc_id"]))["served"]
    if store.transport is None or not hasattr(store.transport, "_s"):
        return None
    import requests
    try:
        h = requests.head(raw_url, allow_redirects=False, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0 (research; InvestmentIntelligenceEngine)"})
    except Exception:
        return None
    req = raw_url.split("/web/")[1][:14]
    served = served_timestamp(h.headers.get("Location", ""), req) if h.status_code in (301, 302, 307, 308) else req
    record_served(store, raw_url, req, served, h.status_code)
    return served


def record_served(store, raw_url, requested, served, status):
    import json as _json
    store.register(raw_url + "#served", _json.dumps({"requested": requested, "served": served, "status": status},
                                                     sort_keys=True).encode(),
                   source_system="wayback", doc_type="wayback_served_check", status=200,
                   title=f"served-capture check {requested} -> {served}")


def fetch_capture(store, cap, company_id=None, run_id=None, title=None):
    """Returns (doc_id, body). Sets cap['served_timestamp'] / cap['served_at'] to the capture actually served
    (redirects to a neighbouring capture happen); callers must date the content with served_at."""
    url = RAW.format(ts=cap["timestamp"], original=cap["original"])
    prev = store.latest(url)
    if prev is not None and prev["http_status"] == 200:
        st = served_check(store, url)
        if st is None:
            cap["served_unverified"] = True
            st = cap["timestamp"]
        cap["served_timestamp"], cap["served_at"] = st, ts_to_utc(st)
        return prev["doc_id"], store.read(prev["doc_id"])
    try:
        f = store.fetch(url, source_system="wayback", doc_type="archived_page", run_id=run_id,
                        company_id=company_id, title=title, published_at=cap["captured_at"],
                        published_at_basis="wayback_capture_timestamp")
        served = served_timestamp(getattr(store.transport, "last_final_url", "") or "", cap["timestamp"])
        cap["served_timestamp"], cap["served_at"] = served, ts_to_utc(served)
        record_served(store, url, cap["timestamp"], served, f.status)
    except FetchError:
        return None, None
    except Exception as e:                         # DNS / connection failures: skip this capture, do not abort
        import sys
        print(f"archive fetch failed ({type(e).__name__}): {url}", file=sys.stderr)
        return None, None
    return f.doc_id, f.body
