"""Build the minimal raw-evidence bundle that reproduces the valuation audit outside this repository.

    .venv/bin/python audit/make_bundle.py OUT_DIR

The bundle holds exactly the documents `independent_check.py --scope=valuation` reads (found by running it with
the raw-byte reader instrumented), split by licence:
  * included   public-domain or own records: SEC companyfacts, U.S. Treasury CSV, ALFRED T10YIE vintage,
               served-capture checks (records written by this project);
  * excluded   third-party content without a redistribution licence: Yahoo chart JSON, Moody's Aaa (FRED, Moody's
               copyright), Damodaran spreadsheets and their Wayback copies. Only URL, sha256 and size are shipped;
               `fetch_restricted.py` downloads them from the original or archived URL and checks the hash.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = {"sec_xbrl_api": "US government work (SEC data.sec.gov API), public domain",
           "us_treasury": "US government work (U.S. Treasury), public domain",
           "alfred": "ALFRED vintage of T10YIE, a series computed by the St. Louis Fed from Treasury yields; "
                     "no third-party copyright noted for this series"}
EXCLUDE = {"yahoo_chart": "Yahoo Finance: no redistribution licence",
           "fred": "Moody's Aaa via FRED: Moody's copyright, reprinted by FRED with permission",
           "damodaran": "A. Damodaran data files: freely published, no redistribution licence stated",
           "wayback": "archived copy of a Damodaran file: same terms as the original"}
STABLE = {"yahoo_chart": False, "damodaran": False}   # live URLs whose bytes change (re-adjusted / monthly update)


def used_documents():
    import runpy
    g = runpy.run_path(str(ROOT / "audit" / "independent_check.py"), run_name="bundle")
    used, orig = set(), g["raw"]

    def raw(doc_id):
        used.add(doc_id)
        return orig(doc_id)
    g["raw"].__globals__["raw"] = raw
    g["valuation_check"]()
    lin = g["lineage_check"]()
    obs = set()
    for name in ("valuation_2026-11-05.json", "valuation_2026-11-05_before_patch.json"):
        doc = json.loads((ROOT / "docs" / "valuation" / name).read_text())
        for c in doc["companies"].values():
            obs.update(c["inputs"]["sources"]["ttm_revenue"]["inputs"])
    return sorted(used), sorted(obs), lin


def main(out: Path):
    docs, obs, _ = used_documents()
    src = sqlite3.connect(str(ROOT / "data" / "iie.sqlite"))
    src.row_factory = sqlite3.Row
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence").mkdir(exist_ok=True)
    dst = sqlite3.connect(str(out / "bundle.sqlite"))
    for table, ids, key in (("source_document", docs, "doc_id"), ("observation", obs, "obs_id")):
        ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        dst.execute(ddl)
        for i in ids:
            r = src.execute(f"SELECT * FROM {table} WHERE {key}=?", (i,)).fetchone()
            dst.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(r.keys()))})", tuple(r))
    dst.commit()
    manifest = []
    for i in docs:
        r = src.execute("SELECT * FROM source_document WHERE doc_id=?", (i,)).fetchone()
        system = r["source_system"]
        own = r["doc_type"] == "wayback_served_check"
        included = own or system in INCLUDE
        if included:
            p = out / "evidence" / r["storage_path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "data" / "evidence" / r["storage_path"], p)
        manifest.append({"doc_id": i, "source_system": system, "doc_type": r["doc_type"], "url": r["url"],
                         "sha256_raw_bytes": r["sha256"], "bytes": r["bytes"], "retrieved_at": r["retrieved_at"],
                         "storage_path": r["storage_path"], "included": included,
                         "licence": "record written by this project (which capture the archive served)" if own
                         else INCLUDE.get(system) or EXCLUDE.get(system),
                         "byte_stable_at_url": True if r["url"].startswith("http://web.archive.org/") or included
                         else STABLE.get(system, True)})
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    for f in ("independent_check.py", "fetch_restricted.py"):
        shutil.copyfile(ROOT / "audit" / f, out / f)
    (out / "audit").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "audit" / "external_10q_figures.json", out / "audit" / "external_10q_figures.json")
    (out / "docs" / "valuation").mkdir(parents=True, exist_ok=True)
    for f in ("valuation_2026-11-05.json", "valuation_2026-11-05_before_patch.json"):
        shutil.copyfile(ROOT / "docs" / "valuation" / f, out / "docs" / "valuation" / f)
    shutil.copyfile(ROOT / "audit" / "BUNDLE_README.md", out / "README.md")
    inc = [m for m in manifest if m["included"]]
    print(f"{len(manifest)} documents: {len(inc)} included ({sum(m['bytes'] for m in inc) / 1e6:.1f} MB raw), "
          f"{len(manifest) - len(inc)} to fetch; {len(obs)} observation rows -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
