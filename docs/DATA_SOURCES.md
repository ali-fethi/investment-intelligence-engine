# Data sources, licensing and point-in-time reliability

| Source | Used for | Licence / terms | Point-in-time reliability |
|---|---|---|---|
| SEC EDGAR submissions API (`data.sec.gov/submissions`) | Filing index, form types, 8-K items, **acceptance timestamps** | US government work, public domain. SEC fair-access policy: ≤10 req/s, descriptive User-Agent with contact (`SEC_USER_AGENT`) | **High.** Acceptance time is UTC; it is cross-checked against the filing header `ACCEPTANCE-DATETIME` (Eastern time), and all 97 downloaded filings match. |
| SEC XBRL `companyfacts` API | Reported financial facts (us-gaap, dei) | Public domain | **High for availability** (each fact carries its accession, joined to the acceptance time). **Limits:** standard-taxonomy, non-dimensional facts only, so company extensions and segment/class members are absent (e.g. per-class share counts; hence no market cap for GOOGL/META). Values are as tagged by the filer. The API is a current compilation, but because every fact keeps its originating accession, history is reconstructable. |
| EDGAR archives (10-K, 10-Q, 8-K EX-99.x, index headers) | Full text for evidence retrieval; earnings-release tables | Public domain | **High** (acceptance time). An earnings release may appear on the issuer's IR site minutes before the 8-K; the 8-K time is a conservative upper bound. |
| Company IR websites (abc.xyz, microsoft.com/investor, investor.atmeta.com) | Microsoft transcripts only (Milestone 2) | Site terms | Two sites returned HTTP 403 to automated requests and none publishes reliable publication timestamps. The same releases are taken from the SEC 8-K exhibits instead. **Gap:** IR-only material (slide decks, transcripts, CEO letters not furnished on 8-K) is not ingested. |
| Yahoo Finance chart endpoint | Daily OHLCV, splits, dividends | **Unofficial, no licence for redistribution, no SLA.** Personal research use only. Replace with a licensed vendor (e.g. CRSP, Refinitiv, Polygon, Databento) before any shared or commercial use | **Not point-in-time.** `close` is split-adjusted as of retrieval; `adjclose` is also dividend-adjusted. Dividends are split-adjusted. History can be silently rewritten: a re-ingest stores a new row and QA reports `vendor_history_rewrites`. Split/dividend **announcement dates are not provided**, so availability is set to the ex-date open (announcement known earlier in reality: conservative). As-traded prices are reconstructed (see ARCHITECTURE). |
| Stooq | Not used | — | Serves a JavaScript bot challenge; bypassing it was out of bounds. |
| U.S. Treasury daily par yield curve CSV | Curve 1M–30Y | Public domain | **Medium.** Current file only, no vintages; Treasury occasionally corrects values. Publication time is **assumed** to be the end of the curve date (`availability_verified=0`). A later correction is stored with `available_at = retrieval time` (`revision_detected_at_retrieval`), never back-dated. |
| FRED (latest vintage) | DGS3MO, DGS2, DGS10, DGS30, DFF, T10YIE | FRED terms; some series carry third-party copyright (check series notes before redistribution) | **Medium-high.** Not revised in practice. Availability is assumed at 17:00 ET one business day after the observation (two for DFF, whose weekend values appear with Monday's release). This was **verified against exact ALFRED vintages at the demo cutoffs** (QA `fred_availability_lag`). The check found and fixed an over-aggressive DFF assumption during this sprint. |
| ALFRED vintages | CPIAUCSL, UNRATE, GDP (revised series) | As FRED | **High, at vintage-date granularity.** Vintages are *sampled* (month-ends since 2019 plus exact cutoff dates). A release between two samples becomes visible only at the next sample, so this is conservative and may lag by up to a month. Add exact vintages with `--vintage`. |
| Benchmarks (Yahoo): SPY, QQQ, XLK, XLC, ^GSPC, ^IXIC | Event-study benchmarks | as Yahoo above | As Yahoo. XLC starts 2018-06-19. |
| Nasdaq.com historical quotes | Independent price cross-check only | Unofficial; validation use only | **Split-adjusted** (not as-traded, contrary to the initial label, now corrected). One stale value found (SPY 2026-04-20). History from 2016-11. |
| FRED SP500 / NASDAQCOM | Independent index cross-check | FRED terms (S&P/Nasdaq copyright) | Same-day close; used for validation only. |
| Federal Reserve FOMC statements | Macro event calendar | Public domain | **High**: release time stated on each statement page. |
| ALFRED vintage dates (CPI, payrolls, GDP, PCE) | Macro release calendar | as FRED | Date verified; time 08:30 ET **assumed**; some vintages are revision-only. |
| EDGAR filing index of 12 context issuers | Overlapping earnings detection | Public domain | High (acceptance). |
| Microsoft IR transcripts | Management guidance | Issuer copyright; private verification only | Page undated; call date stated; call time assumed (grade B). |
| Press (CNBC, Zacks via Yahoo, Motley Fool) | Consensus figures, documented reactions | Copyright; stored privately for quote verification only | Grade B only when published **and last modified** before the event; otherwise C. |
| Intraday / extended-hours prices | **Not available** | Alpha Vantage's intraday history is Premium; no free source found | After-hours and pre-market cannot be separated; intra-session timing around FOMC cannot be measured. |

## Sources that cannot reliably provide point-in-time data (summary)

1. **Vendor price history (Yahoo):** retroactively adjusted and rewritable. The system mitigates this
   by storing raw retrievals and reconstructing as-traded prices; true PIT still needs a licensed
   vendor with as-of history.
2. **Corporate-action announcement dates:** unavailable from the price vendor. Ex-dates only.
3. **Treasury curve:** no vintages; publication time assumed.
4. **Forward-looking event calendar** (scheduled earnings dates): no free source records when a date
   was first announced. It is excluded and listed as a gap in every snapshot.
5. **Revised macro series between sampled vintages:** conservative lag (see above).
6. **Dimensional XBRL facts** (segments, share classes): not in `companyfacts`. They need the full
   XBRL instance documents (planned: parse the `*_htm.xml` instance stored with each filing).
7. **Issuer IR websites:** blocked or untimestamped. The 8-K exhibits are used instead; Microsoft
   transcripts are dated only by their call date.
8. **Sell-side consensus:** no as-of history. Press "vs expected" pages are rewritten after releases
   (grade C); dated previews (grade B) come from a single provider (Zacks).
9. **Extended-hours prices:** no source (see above).

## Known data findings from QA (not errors in the pipeline)

* Microsoft FY2016–FY2017 revenue: `SalesRevenueNet` (pre-ASC 606, e.g. $85.3bn FY2016) disagrees with
  the restated `RevenueFromContractWithCustomerExcludingAssessedTax` ($91.2bn). Both are kept. The
  ASC 606 concept is preferred and the conflict is shown in snapshots.
* Microsoft quarterly operating cash flow is not derivable for FY2016 Q1–Q4 and FY2017 Q1. Those
  cash-flow facts are absent from `companyfacts`, so this is recorded as a gap.
* Q4 diluted EPS is not reported as a three-month figure in 10-Ks. It is available from the earnings
  release only, and is never derived from FY − 9M.
* $1m differences between earnings releases / summed quarters and XBRL year-to-date values come from
  reporting in millions. They are classified `within_rounding`, never silently passed.
* Alphabet 8-K 0001652044-23-000034 (Item 2.02) furnished a blog post rather than a results table.
  It is recorded as a gap.
