"""Download the bundle's licence-restricted documents from their public URLs and check them against the audited bytes.

    python fetch_restricted.py            (run inside the bundle directory; standard library only)

Wayback raw captures (`id_` URLs) are byte-stable: a hash mismatch means the archive served something else.
Yahoo chart JSON and the live Damodaran files change over time (history re-adjusted, monthly updates): a mismatch
there is expected and is reported; the audit then runs on today's bytes and small differences can follow.
"""
import gzip
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (independent audit of a research dataset)"


def main():
    man = json.loads((HERE / "MANIFEST.json").read_text())
    bad = []
    for m in man:
        if m["included"]:
            continue
        p = HERE / "evidence" / m["storage_path"]
        if p.exists():
            continue
        # FRED times out custom agents but answers Python's default one
        req = urllib.request.Request(m["url"], headers={} if "stlouisfed.org" in m["url"] else {"User-Agent": UA})
        try:
            body = urllib.request.urlopen(req, timeout=120).read()
        except Exception as e:                      # noqa: BLE001
            print("FAILED", m["url"], e)
            bad.append(m["url"])
            continue
        sha = hashlib.sha256(body).hexdigest()
        same = sha == m["sha256_raw_bytes"]
        print("identical" if same else ("CHANGED (expected: live source)" if not m["byte_stable_at_url"]
                                         else "MISMATCH"), m["url"][:110])
        if not same and m["byte_stable_at_url"]:
            bad.append(m["url"])
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wb") as f:
            f.write(body)
        time.sleep(1.0)                              # polite to the archive and publishers
    print(f"{len(bad)} problem(s)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
