# Free sources investigated for point-in-time expectations

| Source | What it offers | Point-in-time value | Decision |
|---|---|---|---|
| **MarketWatch analyst-estimates pages, via Internet Archive captures** | FactSet consensus EPS (mean/high/low, sometimes the number of estimates) for the next two quarters; "estimate vs actual" for the last four | **B.** The archive's capture timestamp is independent evidence of publication. Captures are dense for 2016–2022 and sparse after 2022 | **Used** (primary dated consensus EPS). 2016–2019 pages carry no quarter labels: the quarter is assigned point-in-time and checked against EDGAR EPS |
| Zacks previews on Yahoo Finance (also GuruFocus, StockStory, Barchart) | Revenue and EPS consensus in an article dated before the release | **B** when the page was published *and last modified* before the release; quote re-verified by the pipeline | **Used.** URLs found by a research sub-agent; values verified by the pipeline. Alphabet's "revenue" is ex-TAC |
| CNBC "results vs expectations" pages | LSEG / StreetAccount consensus | **C.** Pages are created before the release and rewritten after it | Kept as descriptive only |
| Yahoo Finance analysis pages, via Internet Archive | EPS and revenue consensus tables | Inconsistent: the Nov 2023 capture shows Sep-2023 EPS 2.50 and revenue $51.4bn vs ~2.65 and ~$54.5bn elsewhere. The "current quarter" stays on the already-reported quarter, a leakage trap | **Rejected** as a source; documented |
| Zacks quote pages, via Internet Archive | Detailed estimates | Captures return empty bodies | Not usable |
| Estimize | Crowd estimates | No archive captures found | Not usable |
| Nasdaq.com earnings-surprise API | Consensus vs actual for the last 4 quarters | **C** (as-of date unknown) | Not used historically |
| **Nasdaq.com earnings-forecast API** | Current consensus EPS (mean/high/low/n) for the next 5 quarters and 4 years | **B going forward:** each snapshot is dated by our retrieval | **Used:** `iie expect forward`. First snapshot taken 2026-11-06 |
| **Meta 8-K exhibits (CFO outlook)** | Revenue, total-expense and capex ranges, plus revisions | **A** (EDGAR acceptance) | **Used.** Outlook present in 25 of 44 releases since 2016 |
| **Microsoft IR transcripts** | Segment revenue ranges and Azure growth in constant currency | **B.** The call date is printed on the page; call time assumed | **Used** for FY2022 Q3 onward. Earlier transcripts return 404 (gap) |
| **Alphabet 8-K exhibits** | No revenue or earnings guidance; capex guidance in Feb 2025, Oct 2025 and Feb 2026 | **A** | **Used.** Capex guidance given only on calls (e.g. Jul 2025) is a documented gap |
| **CBOE VXGOG (FRED)** | 30-day implied volatility on Google options | **B** (CBOE close) | **Used** for the implied earnings move. No free equivalent exists for MSFT or META |
| Alpha Vantage (intraday, options) | Extended hours, options | Premium | Not used: paid |
