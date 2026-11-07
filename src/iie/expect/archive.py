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


def fetch_capture(store, cap, company_id=None, run_id=None, title=None):
    url = RAW.format(ts=cap["timestamp"], original=cap["original"])
    prev = store.latest(url)
    if prev is not None and prev["http_status"] == 200:
        return prev["doc_id"], store.read(prev["doc_id"])
    try:
        f = store.fetch(url, source_system="wayback", doc_type="archived_page", run_id=run_id,
                        company_id=company_id, title=title, published_at=cap["captured_at"],
                        published_at_basis="wayback_capture_timestamp")
    except FetchError:
        return None, None
    except Exception as e:                         # DNS / connection failures: skip this capture, do not abort
        import sys
        print(f"archive fetch failed ({type(e).__name__}): {url}", file=sys.stderr)
        return None, None
    return f.doc_id, f.body
