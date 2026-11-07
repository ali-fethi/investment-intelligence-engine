"""Content-addressed evidence store + HTTP fetcher that records provenance for every byte.

Every fetch produces one immutable `source_document` row: URL, HTTP status, retrieval time,
sha256 of the raw bytes and the path of the stored (gzipped) copy.  Downstream parsers read
the stored bytes, never the network, so any observation can be traced back to the exact
bytes it was parsed from.
"""
from __future__ import annotations

import gzip
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from .config import EVIDENCE_DIR, GENERIC_USER_AGENT, RATE_LIMITS, SEC_USER_AGENT
from .timeutil import utcnow

LICENSES = {
    "sec_edgar": "US government work (SEC EDGAR); public domain. SEC fair-access policy applies.",
    "sec_xbrl_api": "US government work (SEC data.sec.gov APIs); public domain.",
    "us_treasury": "US government work (U.S. Treasury); public domain.",
    "fred": "FRED (St. Louis Fed). Series may carry third-party copyright; check series notes before redistribution.",
    "alfred": "ALFRED (St. Louis Fed) vintage data; same terms as FRED.",
    "federal_reserve": "US government work (Federal Reserve Board); public domain.",
    "nasdaq_api": "Nasdaq.com historical quotes endpoint (unofficial). Used only to cross-check prices; "
                  "no redistribution.",
    "issuer_ir": "Issuer investor-relations website; issuer copyright. Stored for private verification only.",
    "press": "News publisher; copyrighted. Stored for private verification of quoted figures only; "
             "never redistributed.",
    "yahoo_chart": "Yahoo Finance unofficial chart endpoint. No licence for redistribution; personal research use "
                   "only; no SLA; history is silently re-adjusted.",
}


# Hosts that refuse non-browser clients. Values are request headers only; nothing here bypasses an
# access control (no cookies, no challenge solving).
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
HOST_HEADERS = {
    "api.nasdaq.com": {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*",
                       "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"},
    "www.federalreserve.gov": {"User-Agent": BROWSER_UA},
    "www.cnbc.com": {"User-Agent": BROWSER_UA},
    "www.microsoft.com": {"User-Agent": BROWSER_UA},
    "finance.yahoo.com": {"User-Agent": BROWSER_UA},
    "www.fool.com": {"User-Agent": BROWSER_UA},
}


@dataclass
class Fetched:
    doc_id: int
    url: str
    body: bytes
    sha256: str
    retrieved_at: str
    status: int


class Transport:
    """Real HTTP transport (requests). Tests substitute a dict-backed fake."""

    def __init__(self):
        import requests
        self._s = requests.Session()
        self._last: dict = {}

    def get(self, url: str) -> tuple[int, bytes, str]:
        host = urlparse(url).netloc
        wait = RATE_LIMITS.get(host, 0.5) - (time.monotonic() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        ua = SEC_USER_AGENT if "sec.gov" in host else GENERIC_USER_AGENT
        headers = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
        headers.update(HOST_HEADERS.get(host, {}))
        last_exc = None
        for attempt in range(4):
            try:
                r = self._s.get(url, headers=headers, timeout=60)
                self._last[host] = time.monotonic()
                self.last_final_url = r.url              # after redirects (e.g. the capture the archive served)
                self.last_headers = dict(r.headers)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                return r.status_code, r.content, r.headers.get("Content-Type", "")
            except Exception as e:  # network hiccup
                last_exc = e
                time.sleep(2 ** attempt)
        if last_exc:
            raise last_exc
        return r.status_code, r.content, r.headers.get("Content-Type", "")


class FetchError(RuntimeError):
    pass


class ReplayTransport:
    """Serves bytes from a previous database's evidence store, in retrieval order per URL, and exposes the
    original retrieval timestamp so a rebuilt database keeps the same system-time (knowledge) history."""

    def __init__(self, old_db_path, evidence_root: Path = EVIDENCE_DIR):
        import sqlite3
        self.old = sqlite3.connect(str(old_db_path))
        self.old.row_factory = sqlite3.Row
        self.root = Path(evidence_root)
        self.cursor: dict = {}
        self.last_retrieved_at = None

    VOLATILE_PARAMS = ("period2",)   # request-time parameters that differ between runs (e.g. 'until now')

    def _strip(self, url: str) -> str:
        import re
        for p in self.VOLATILE_PARAMS:
            url = re.sub(rf"([?&]){p}=[^&]*&?", r"\1", url)
        return url

    def get(self, url: str):
        rows = self.old.execute("SELECT * FROM source_document WHERE url=? ORDER BY doc_id", (url,)).fetchall()
        if not rows:
            key = self._strip(url)
            rows = [r for r in self.old.execute("SELECT * FROM source_document WHERE url LIKE ? ORDER BY doc_id",
                                                (key.split("?")[0] + "%",)) if self._strip(r["url"]) == key]
        i = self.cursor.get(url, 0)
        if not rows:
            self.last_retrieved_at = None
            return 404, b"not in evidence store", "text/plain"
        r = rows[min(i, len(rows) - 1)]
        self.cursor[url] = i + 1
        with gzip.open(self.root / r["storage_path"], "rb") as f:
            body = f.read()
        if hashlib.sha256(body).hexdigest() != r["sha256"]:
            raise FetchError(f"evidence corrupted for {url}")
        self.last_retrieved_at = r["retrieved_at"]
        return r["http_status"], body, r["content_type"] or ""


class EvidenceStore:
    def __init__(self, conn, root: Path = EVIDENCE_DIR, transport: Optional[Transport] = None):
        self.conn = conn
        self.root = Path(root)
        self.transport = transport

    # -- storage ---------------------------------------------------------------------------
    def _path_for(self, sha: str) -> Path:
        return self.root / sha[:2] / f"{sha}.gz"

    def put_bytes(self, body: bytes) -> tuple[str, Path]:
        sha = hashlib.sha256(body).hexdigest()
        p = self._path_for(sha)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            with gzip.open(tmp, "wb") as f:
                f.write(body)
            tmp.rename(p)
        return sha, p

    def read(self, doc_id: int) -> bytes:
        row = self.conn.execute("SELECT storage_path, sha256 FROM source_document WHERE doc_id=?", (doc_id,)).fetchone()
        if row is None:
            raise KeyError(doc_id)
        with gzip.open(self.root / row["storage_path"], "rb") as f:
            body = f.read()
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise FetchError(f"evidence corrupted for doc {doc_id}")
        return body

    # -- fetching --------------------------------------------------------------------------
    def fetch(self, url: str, *, source_system: str, doc_type: str, run_id=None, company_id=None,
              accession_no=None, title=None, published_at=None, published_at_basis=None,
              accept: Callable[[int, bytes], bool] = lambda s, b: s == 200) -> Fetched:
        if self.transport is None:
            raise FetchError("no transport configured (offline mode)")
        status, body, ctype = self.transport.get(url)
        retrieved_at = getattr(self.transport, "last_retrieved_at", None) or utcnow()
        if not accept(status, body):
            raise FetchError(f"{url} -> HTTP {status} ({len(body)} bytes)")
        return self.register(url, body, source_system=source_system, doc_type=doc_type, status=status,
                             content_type=ctype, retrieved_at=retrieved_at, run_id=run_id, company_id=company_id,
                             accession_no=accession_no, title=title, published_at=published_at,
                             published_at_basis=published_at_basis)

    def register(self, url, body: bytes, *, source_system, doc_type, status=200, content_type=None,
                 retrieved_at=None, run_id=None, company_id=None, accession_no=None, title=None,
                 published_at=None, published_at_basis=None) -> Fetched:
        sha, path = self.put_bytes(body)
        retrieved_at = retrieved_at or utcnow()
        cur = self.conn.execute(
            """INSERT INTO source_document(source_system, doc_type, url, company_id, accession_no, title, sha256,
                   bytes, content_type, storage_path, http_status, published_at, published_at_basis, retrieved_at,
                   license_note, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source_system, doc_type, url, company_id, accession_no, title, sha, len(body), content_type,
             str(path.relative_to(self.root)), status, published_at, published_at_basis, retrieved_at,
             LICENSES.get(source_system), run_id))
        return Fetched(cur.lastrowid, url, body, sha, retrieved_at, status)

    def latest(self, url: str):
        """Most recent stored retrieval of a URL (for offline re-parsing)."""
        return self.conn.execute(
            "SELECT * FROM source_document WHERE url=? ORDER BY doc_id DESC LIMIT 1", (url,)).fetchone()
