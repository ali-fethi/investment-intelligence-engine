# Raw-evidence bundle: reverse DCF v2.1 audit (cutoff 5 Nov 2026)

Reproduces, outside the repository, the independent recomputation of the patched valuation: TTM inputs and
their lineage, beta, WACC with the point-in-time monthly ERP, sales-to-capital, and implied revenue growth
under the ROIC fade, the Sprint 2.1 abrupt case, and scenarios C and D.

```
python3 -m venv v && v/bin/pip install pandas xlrd openpyxl
v/bin/python fetch_restricted.py                       # downloads the documents not redistributed here
IIE_AUDIT_ROOT=. v/bin/python independent_check.py --scope=valuation
```

The result is `audit/results_valuation.json`. For each company it compares the independent figures with
`docs/valuation/valuation_2026-11-05.json` (patched engine output). The lineage check also reads
`..._before_patch.json` (Sprint 2.1 output).

| Content | In the bundle? | Why |
|---|---|---|
| SEC companyfacts JSON (3), U.S. Treasury 2026 yield CSV, ALFRED T10YIE vintage of 5 Nov 2026 | yes, the bytes as retrieved (gzip) | public domain / no third-party copyright noted |
| Served-capture checks for archived files | yes | records written by this project |
| 30 XBRL observation rows (TTM revenue inputs, before and after the patch) | yes, in `bundle.sqlite` | extracted from SEC data |
| Yahoo chart JSON (GOOGL, MSFT, META, SPY) | no: URL + sha256 | no redistribution licence; the live history is re-adjusted, so today's bytes differ |
| Moody's Aaa (FRED AAA) | no: URL + sha256 | Moody's copyright |
| Damodaran `ERPbymonth.xlsx` (current file + 23 Wayback captures) and `histimpl.xls` (current file) | no: URL + sha256 | no redistribution licence stated; Wayback `id_` copies are byte-stable and hash-checked |

`MANIFEST.json` lists every document with its URL, sha256 of the raw bytes, size, retrieval time, licence and
whether its URL is byte-stable. The audit script imports nothing from the engine.
