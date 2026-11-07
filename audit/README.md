# Audit material (Sprint 2.1)

| File | What it is |
|---|---|
| `independent_check.py` | Re-computation from the raw source files in `data/evidence/`. It imports no calculation function from `iie`. Run it with `.venv/bin/python audit/independent_check.py` |
| `results_valuation.json` | Output of `independent_check.py --scope=valuation`: the part reproducible from the external bundle (valuation, TTM lineage, 10-Q figures) |
| `make_bundle.py`, `fetch_restricted.py`, `BUNDLE_README.md` | Builds the minimal raw-evidence bundle for external reproduction; restricted documents are shipped as URL + sha256 and re-downloaded |
| `results.json` | Output of the last run: valuation inputs and implied growth, EPS surprises, abnormal returns, accounting checks, look-ahead checks, served-capture checks, and the comparison with the 10-Q figures |
| `external_10q_figures.json` | Figures read directly from the 10-Qs (Q3 2026) by a separate auditor process with no repository access |
| `external_check_files.sha256` | Hashes of the documents that auditor downloaded (kept in `data/audit_external/`, git-ignored because the archived pages and articles are copyrighted) |

The served-capture checks for all archived consensus pages are stored as evidence rows
(`source_document.doc_type = 'wayback_served_check'`). The raw list is in `data/wayback_redirect_check.json`.
