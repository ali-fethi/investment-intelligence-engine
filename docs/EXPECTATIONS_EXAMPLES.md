# Independently checked historical examples

For each release, every expectation shown was public **before** the release's EDGAR acceptance (grade A/B).
Each one is cross-checked against at least one independent source. Grade-C figures (documented after the
event) appear only as checks. Full rows: `expectations/events.json`.

## 1. Microsoft FY2024 Q2 (public 30 Jan 2024 16:03 ET)

| Class | Point-in-time value | Independent check |
|---|---|---|
| Consensus EPS | $2.76, Zacks, 24 Jan (6 days before) | FactSet on MarketWatch, archived 20 Dec 2023: $2.76. Zacks 16 Jan: $2.75. LSEG via CNBC (C): $2.78 |
| Consensus revenue | $61.03bn, Zacks | LSEG via CNBC (C): $61.12bn |
| Guidance (call of 24 Oct 2023, B) | Intelligent Cloud $25.1–25.4bn; Azure +26–27% in constant currency | Extracted automatically from the transcript; identical to the values verified by hand in Sprint 1 |
| Reported | GAAP EPS $2.93 (street $2.93); revenue $62.02bn; Azure +28% in constant currency (8-K) | Release vs later XBRL 10-Q: exact |
| Surprise | EPS +6.2%, revenue +1.6%, Azure above range | — |
| Market | d0 −2.69%, abnormal −0.86% (−0.7σ); price-implied growth revised −0.4 pt | FOMC 14:00 on d0; GOOGL and AMD the same evening: attribution **high** |

A clear beat on every dated expectation was followed by a reaction indistinguishable from market noise
on FOMC day. The data does not show why.

## 2. Meta FY2022 Q3 (26 Oct 2022 16:19 ET)

| Class | Point-in-time value | Independent check |
|---|---|---|
| Consensus EPS | $1.90, FactSet archived the day of the release (before 16:19); $1.93 on 9 Sep (revision −1.6%) | Zacks 21 Oct: $1.82. Refinitiv via CNBC (C): $1.89 |
| Guidance (8-K, 27 Jul 2022, A) | Q3 revenue $26–28.5bn | Same range quoted by Sprint 1's verified registry |
| Reported | Revenue $27.71bn (within the range, +1.0% vs Zacks); EPS $1.64 (−13.7% vs FactSet, street = GAAP) | Street actual $1.64 on the archived page after the release |
| Guidance issued with the event | 2023 total expenses $96–101bn; 2023 capex $34–39bn | Both parsed from the release. The capex sentence ("For 2023, we expect…") is caught by the parser's variant pattern |
| Market | d0 −24.6%, abnormal −23.4% (−9.3σ). Price-driven implied-growth revision −4.0 pts, offset +4.1 pts by the new, lower FCF | GDP release 08:30 on d0 (tier 2); SPY −0.5% |

## 3. Alphabet FY2023 Q3 (24 Oct 2023 16:01 ET)

| Class | Point-in-time value | Independent check |
|---|---|---|
| Consensus EPS | $1.45, Zacks, 20 Oct; revised +7.4% from FactSet's $1.35 archived in June | LSEG via CNBC (C): $1.45 |
| Consensus revenue | $63.13bn **ex-TAC** (not comparable with GAAP $76.69bn) | Growth-rate check by the research agent confirms the ex-TAC basis |
| Guidance | None for revenue or earnings (release scanned, A) | — |
| Options-implied move | 7.0% (VXGOG, model) | Realised d0 move −9.5%: larger than implied |
| Market | Abnormal −7.5% (−4.8σ) | MSFT reported the same evening and rose: a common market factor is unlikely |

## 4. Meta FY2023 Q4 (1 Feb 2024 16:10 ET)

| Class | Point-in-time value | Independent check |
|---|---|---|
| Consensus | EPS $4.83 and revenue $38.99bn, Zacks, 31 Jan | Zacks 30 Jan: $4.84 / $38.93bn. LSEG via CNBC (C): $4.96 / $39.18bn |
| Guidance (8-K, 25 Oct 2023, A) | Revenue $36.5–40bn | — |
| Reported | Revenue $40.11bn, **above** the top of guidance (+4.9% vs midpoint, +2.9% vs Zacks); GAAP EPS $5.33 (+10.4%); first dividend | — |
| Market | Abnormal +18.1% (+9.0σ). Price-driven implied-growth revision +2.1 pts | Payrolls 08:30 on d0, but SPY +1.05%; AMZN and AAPL the same evening |

## 5. Microsoft FY2025 Q1 (30 Oct 2024 16:04 ET)

| Class | Point-in-time value | Independent check |
|---|---|---|
| Consensus EPS | $3.08, Zacks, 25 Oct; revised −2.5% from FactSet's $3.16 (22 Jul) via $3.10 (26 Sep) | LSEG via CNBC (C): $3.10 |
| Guidance (call of 30 Jul 2024, B) | Azure +28–29% in constant currency; segment ranges on the old segment definitions | Microsoft recast its segments in Aug 2024, so these ranges are not comparable with the reported segments |
| Reported | GAAP EPS $3.30 (+7.1%); revenue $65.59bn (+1.8%); Azure +34% in constant currency (above range) | — |
| Guidance issued with the event | Azure FY25 Q2 +31–32% in constant currency | No dated pre-event consensus for Q2 Azure: the "disappointing guidance" narrative (CNBC, C) cannot be tested |
| Market | Abnormal −3.9% (−4.4σ) | META the same evening; PCE 08:30 on d0 |
