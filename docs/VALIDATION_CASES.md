# Five independent historical validation cases

Each case runs through the same engine as the Microsoft case and is checked against sources independent
of the primary pipeline:

- the EDGAR filing header (a second timestamp source);
- later XBRL filings (vs the parsed release);
- Nasdaq.com closes (vs Yahoo);
- FRED index levels (vs Yahoo indices);
- dated news reports (vs measured moves).

The full reports are in [`event_engine/`](event_engine/).

| # | Event (public, ET) | d0 | Return | SPY | MM abnormal | SCAR | Gap / session | Attribution | Checks |
|---|---|---|---:|---:|---:|---:|---|---|---|
| 1 | GOOGL Q4-23 (30 Jan 2024 16:01) | 31 Jan 2024 | −7.50% | −1.63% | −5.30% | −3.5 | −5.18% / −2.45% | high (FOMC d0; MSFT, AMD same evening) | 9 pass, 1 n/a |
| 2 | META Q4-23 (1 Feb 2024 16:10) | 2 Feb 2024 | +20.32% | +1.05% | +18.11% | +9.0 | +16.42% / +3.35% | high (payrolls 08:30 d0; AMZN, AAPL same evening) | 9 pass, 1 n/a |
| 3 | META Q3-22 (26 Oct 2022 16:19) | 27 Oct 2022 | −24.56% | −0.53% | −23.44% | −9.3 | −24.53% / −0.04% | medium (GDP 08:30 d0) | 10 pass |
| 4 | GOOGL Q3-23 (24 Oct 2023 16:01) | 25 Oct 2023 | −9.51% | −1.44% | −7.53% | −4.8 | −7.67% / −1.99% | medium (MSFT, SNAP same evening) | 10 pass |
| 5 | MSFT Q1-FY25 (30 Oct 2024 16:04) | 31 Oct 2024 | −6.05% | −1.96% | −3.89% | −4.4 | −3.97% / −2.17% | medium (PCE 08:30 d0; META same evening) | 10 pass |

"n/a" means Q4 diluted EPS: 10-Ks report EPS for the full year only, so the release value has no
three-month XBRL counterpart. It is never derived from full year minus nine months.

Each documented next-session move matches the measured close-to-close return:

| Case | Reported | Measured |
|---|---|---|
| GOOGL Q4-23 | "more than 7%" | −7.50% |
| META Q4-23 | "more than 20%" | +20.32% |
| META Q3-22 | "24.5%" | −24.56% |
| GOOGL Q3-23 | "9.5%" | −9.51% |
| MSFT Q1-FY25 | "more than 6%" | −6.05% |

## Expectations available per case

| Case | Grade A/B (point-in-time) | Grade C (post-event, descriptive) |
|---|---|---|
| GOOGL Q4-23 | No-guidance policy (8-K, A); Zacks EPS $1.60, Cloud $9.04bn, revenue ex-TAC $70.71bn (B) | LSEG revenue/EPS; StreetAccount Cloud, ads |
| META Q4-23 | Meta guidance $36.5–40bn (8-K, A): actual $40.1bn, **above** range; Zacks $38.93bn / $4.84 (B) | LSEG $39.18bn / $4.96 |
| META Q3-22 | Meta guidance $26–28.5bn (8-K, A): actual $27.71bn, **within**; Zacks $27.44bn / $1.82 (B): EPS −9.9% vs Zacks | Refinitiv $27.38bn / $1.89 |
| GOOGL Q3-23 | No-guidance policy (A); Zacks EPS $1.45, Cloud $8.54bn (B): actual Cloud not ingested | LSEG; StreetAccount Cloud $8.64bn |
| MSFT Q1-FY25 | Azure guidance 28–29% cc (B): actual 34% cc, **above**; Zacks $64.41bn / $3.08 (B) | LSEG $64.51bn / $3.10; Azure 29.4% (StreetAccount) vs 32.8% (CNBC): the two providers disagree |

## Inconsistencies found and fixed during validation

1. **8-K event dates.** Alphabet's Q3-23 8-K (also Item 5.02) carries report date 2023-10-18; it was
   published on 2023-10-24. Events now use the acceptance date. 46 of 701 earnings events were affected.
   Reaction timing was never affected, because it always used acceptance.
2. **FOMC decisions.** The parser missed "decided today to lower … by 1/2 percentage point, to …"
   (e.g. the 2020-03-03 emergency cut). It is fixed, and decisions are re-read from the stored statement
   pages. Five Fed releases have no rate decision (framework review, facilities) and are labelled as such.
3. **Nasdaq.com adjustment.** History was assumed as-traded but is split-adjusted. Detected via GOOGL's
   20:1 split and XLK's 2:1 split; the correction is recorded in `data_correction`.
4. **Stale vendor price.** Nasdaq.com repeats the previous close for SPY on 2026-04-20. Yahoo and the
   FRED S&P 500 level agree with each other; this is classified automatically as `nasdaq_stale_repeat`.
5. **Partial bars.** Bars retrieved before the session closed (today's, at 10:30 ET) are now rejected at
   ingest and filtered at read.
6. **Press pages rewritten after the event.** Pages first published pre-release but modified afterwards
   are graded C, not B.
