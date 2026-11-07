# Independent verification report (Sprint 2.1)

Two separate processes were used. Neither reuses the engine's calculation functions.

1. **Document-level auditor:** a separate agent with no access to this repository. It read the public
   documents directly from SEC EDGAR, the Internet Archive and Yahoo Finance. Its figures are in
   `audit/external_10q_figures.json`. The raw files it downloaded are in `data/audit_external/`
   (git-ignored), with their hashes in `audit/external_check_files.sha256`.
2. **Re-computation script:** `audit/independent_check.py` (output `audit/results.json`). It reads the
   raw stored source bytes (SEC JSON, Yahoo JSON, Treasury/FRED CSV, Damodaran XLS, archived HTML) and
   re-implements every calculation.

Both re-implementations were written by the same author as the engine. The document-level auditor is
the only fully independent check.

## Results

| Check | Sample | Result |
|---|---|---|
| Balance-sheet inputs vs the 10-Q (cash and short-term investments, debt, finance leases) | 3 companies | **Identical** (e.g. MSFT finance leases $70,973m; GOOGL debt $126,118m; META cash $82,449m) |
| Share count vs the 10-Q cover page | 3 companies | MSFT identical. GOOGL +0.6% and META +0.7%: the engine uses the diluted weighted average, because class-level cover counts are not in companyfacts. **Flagged in every valuation** |
| EDGAR acceptance timestamps (filing headers fetched again) | 6 filings, 2019–2025 | **6/6 identical** |
| Archived consensus pages: value and capture date | 8 captures | 7/8 identical. **1 served a different capture than requested** (see below) |
| Dated previews: value, datePublished, dateModified | 4 articles | **4/4 identical**; none modified after publication |
| Valuation re-computation from raw files (TTM, beta, WACC, sales-to-capital, DCF with a different solver) | 3 companies, 5 Nov 2026 | **Identical to machine precision.** A first run differed for GOOGL; the fault was in the audit script (a revenue tag Alphabet stopped using in 2025) |
| EPS consensus re-read from raw archived HTML with a different parser; GAAP EPS from raw XBRL | 12 releases | **12/12 identical** |
| Abnormal return recomputed from raw price JSON | 8 releases | **Maximum difference 0.0** |
| Accounting consistency: net income ≈ diluted EPS × diluted shares (raw XBRL) | MSFT 55, META 47 quarters | 0 exceptions for MSFT. 7 for META, all 2011–2012 (pre-IPO two-class method), outside the analysis window |
| Look-ahead: every expectation used is available before the release | 133 releases | **0 violations** |
| Look-ahead: valuation inputs available by the cutoff | 3 companies | **0 violations** |
| Archived captures used: the capture the archive actually serves predates the release | 81 consensus values | **0 violations after correction**; 0 unverified |
| Sprint 1 snapshots rebuilt (`iie verify`) | 3 snapshots | **3/3 identical** |
| Event Reaction Engine case studies recomputed at their knowledge time | 6 cases | **6/6 identical** (code version field substituted) |
| QA suite (`iie qa`) | full database | 0 failures |
| Automated tests | 70 offline + 3 network | all pass |

## The material finding: archive redirects (look-ahead the earlier audit could not see)

**What happens.** The Internet Archive can answer a request for a capture with a redirect to a
*neighbouring* capture. The collector followed redirects and dated the content with the *requested*
timestamp.

**How it was found and measured.** The document-level auditor found one case. Checking every one of the
194 captures used showed 12 redirects:

- 9 served an **earlier** capture: harmless, now re-dated;
- 1 served a later capture that still predates its release: re-dated;
- **2 served a capture made after the release.** These are Alphabet FY2017 Q4 (requested 31 Jan 2018,
  served 2 Feb 2018; release 1 Feb) and Alphabet FY2019 Q2 (requested 24 Jul 2019, served 25 Jul 2019
  21:00 UTC; release 20:01 UTC). That is genuine look-ahead in two consensus values.

**Fix.**
- The collector now records the capture actually served (final URL after redirects). It stores a
  per-capture "served check" as evidence, dates content with the served capture, and refuses any capture
  served after the release.
- The expectations table was rebuilt from stored evidence.
- Two tests reproduce both cases (later → excluded; earlier → re-dated).

## Other defects found and fixed in this sprint

1. **Damodaran ERP parsed from HTML.** It took the DDM column on long rows (2016: 5.54% instead of
   5.69%), and the page stops at 2016. Replaced by the spreadsheet column "Implied ERP (FCFE)". The old
   series is retained but unused, with a `data_correction` record.
2. **Valuation inputs.** Microsoft has no quarterly depreciation before FY2025, so sales-to-capital now
   uses complete fiscal years. Alphabet's pre-2021 depreciation uses a company-specific XBRL tag, so the
   model reports "not computed" for those releases rather than approximating.
3. **Staleness filter.** A 0-day-old consensus was treated as missing (`0 or 99` in Python), which shrank
   the "fresh" samples. Fixed, with a regression test.
4. **Unit-test mistake (mine).** A test changed the market cap after computing the WACC. The model was
   right; the test was corrected.

## Not verified / unresolved

- Damodaran's past values cannot be checked against their original vintages.
- Moody's Aaa is used as the cost of debt for all three companies. Actual ratings: MSFT AAA, GOOGL AA+,
  META AA−. The debt weight is ≤5%.
- The vendor's street EPS definition (FactSet contributors) is not documented per quarter. It is inferred
  from GAAP differences that each have a known cause.
- One archived capture in the external sample could not be served at its timestamp. It remains labelled
  by the capture actually served.
