# Microsoft, 30 January 2024: corrected event analysis

Full machine-generated report with every number and its evidence:
[`event_engine/case_MSFT_2024-01-30.md`](event_engine/case_MSFT_2024-01-30.md). This page summarises it and
states what changed from the earlier conversational answer.

## What changed from the earlier answer

| Earlier answer | Corrected |
|---|---|
| Reaction given as raw price change only (−2.7% on 31 Jan, then recovery). | Raw −2.69% ≈ **−0.86% abnormal** after market-model adjustment (β 1.20 vs SPY). That is **−0.7σ**, normal noise. Versus QQQ −0.74 pt, versus XLK −0.59 pt. |
| No split between overnight and session. | Overnight gap (after-hours + pre-market, not separable) **−0.40%**, in line with SPY's −0.46% and smaller than QQQ's −1.05%. Regular session **−2.30%** (SPY −1.17%). Most of the decline came during the session in which the FOMC statement came out at 14:00. |
| FOMC mentioned as plausible context, "not verified". | FOMC statement time read from the Fed's release page: **14:00 ET 31 Jan 2024**, hold at 5¼–5½%. Measured S&P 500 −1.611% and Nasdaq −2.230%, matching both FRED and the dated CNBC report. |
| Other earnings not considered. | Same evening: **GOOGL (16:01 ET) and AMD (16:16 ET)**; MSFT's 10-Q at 16:05. Days d1–d4: QCOM, AMZN, META, AAPL; payrolls on 2 Feb. Attribution uncertainty: **high**. |
| Consensus from memory/press (LSEG $2.78 / $61.12bn, Azure 27.5%). | Kept, but labelled **grade C**: the CNBC page was first published at 12:00 ET and **rewritten after the release**, so its "expected" figures are not verifiable point-in-time. Dated pre-event consensus (**grade B**, Zacks, 24 Jan): EPS $2.76, revenue $61.03bn. |
| Guidance cited for Azure only. | Prior guidance (24 Oct 2023 call, grade B): Azure +26–27% cc, Intelligent Cloud $25.1–25.4bn, Productivity $18.8–19.1bn, More Personal Computing $16.5–16.9bn. Actual: Azure +28% cc and Intelligent Cloud $25.9bn, **both above range**. The other two segment actuals are not ingested; this is a gap, not inferred. |
| — | Guidance issued **with** the event (30 Jan call): FY24 Q3 Intelligent Cloud $26.0–26.3bn, Productivity $19.3–19.6bn, More Personal Computing $14.7–15.1bn, Azure "stable" in cc. **No dated consensus for FY24 Q3 was found**, so "light guidance" (CNBC) is a characterisation, not a measured surprise. |

## Measured reaction (total returns)

| Window | MSFT | SPY | QQQ | XLK | ^GSPC | ^IXIC | MM abnormal | SCAR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Overnight gap (close 30 Jan → open 31 Jan) | −0.40% | −0.46% | −1.05% | −0.95% | n/a | n/a | +0.15% (β-adj.) | — |
| Regular session 31 Jan | −2.30% | −1.17% | −0.92% | −1.16% | n/a | n/a | −0.90% (β-adj.) | — |
| d0 close-to-close | −2.69% | −1.63% | −1.96% | −2.10% | −1.61% | −2.23% | −0.86% | −0.72 |
| d0…d+4 | −0.76% | +0.63% | +0.54% | −0.07% | +0.59% | +0.64% | −2.04% | −0.76 |
| d+1…d+19 | +2.74% | +4.84% | +4.39% | +3.55% | +4.62% | +5.17% | −5.04% | −0.97 |

After-hours and pre-market cannot be measured separately: there is no licensed intraday history. The
documented after-hours move ("as much as 2% lower", CNBC) was an intraday extreme, and the
overnight gap that was measured to the 31 Jan open was −0.40%.

## Documented vs inferred

- **Documented** (dated sources): the figures above; the FOMC hold and its wording; CNBC's attribution
  ("slipped nearly 3% … on lower-than-expected forward guidance"). That attribution is the publisher's
  and does not quote a consensus for the guidance.
- **Inference (hypothesis)**: the 31 Jan decline is mostly explained by market and tech-sector moves
  on FOMC day. Microsoft's own abnormal component is within normal noise. Separating the pre- and
  post-14:00 parts of the session would need intraday data (see the PM decision brief).

## Independent validation (10/10 pass)

The EDGAR acceptance time matches the filing header. The release's revenue, operating income, net
income and EPS match the later 10-Q XBRL exactly. The Yahoo closes match Nasdaq.com on both days. The
^GSPC and ^IXIC daily returns match FRED to 0.001 pt. The measured move is consistent with the dated news
report.
