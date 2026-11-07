# Where the market may be pricing something our fundamentals do not show (hypotheses)

Cutoff: close of 5 Nov 2026. All inputs are point-in-time. The method restates each price as the 10-year
FCF growth it requires (reverse DCF):

- discount rate = 10-year Treasury 4.84% + an equity risk premium of 4%, 5% or 6%;
- terminal growth 3%;
- FCF = trailing-twelve-month operating cash flow − capex.

That growth figure is then compared with what extrapolating reported fundamentals gives. **No forecast is
made, and no fundamental number is adjusted to fit the price.**

| | GOOGL | MSFT | META |
|---|---:|---:|---:|
| Market value (as-traded close × diluted shares) | $3,495bn | $3,991bn | $1,557bn |
| TTM free cash flow | $19.7bn | $60.0bn | $28.6bn |
| Capex as share of operating cash flow (TTM) | 89% | 69% | 78% |
| **Price-implied FCF growth, 10 years** (ERP 4% / 5% / 6%) | 34% / **37%** / 40% | 20% / **23%** / 25% | 18% / **20%** / 23% |
| Trailing 3-year FCF growth (per year) | −37% | −2% | −9% |
| Trailing 3-year revenue growth (per year) | 17% | 17% | 24% |
| Latest capex guidance (point-in-time) | FY2026 $175–185bn (Feb 2026) | not issued as a figure in the release | FY2026 $135–145bn (raised from $115–135bn over the year) |

## Hypotheses (to investigate, not conclusions)

1. **The price treats current capex as growth investment, not as a permanent cost.**
   - Extrapolating trailing FCF (falling at all three) cannot justify these prices under any ERP in the grid.
   - The price is consistent only with FCF growth of roughly 20–37% a year for a decade, i.e. a large share
     of today's capex turning into future cash flow.
   - What would test this: depreciation lives and impairments, cloud and AI segment margins, capex
     intensity once construction slows, and a maintenance-vs-growth capex split where disclosed.
2. **Alphabet is the most extreme case.** Its FCF is the most depressed (capex 89% of operating cash
   flow), so its implied growth is the most sensitive to how quickly capex normalises. A modest change in
   assumed normalised capex moves the implied growth by many points. The figure is unstable, not precise.
3. **The market's expectation shows up in reactions to capex news, not only to EPS.**
   - Over 2020–2026, street EPS surprises correlate only moderately with abnormal returns
     (Spearman +0.42, n = 43).
   - Large moves occurred with EPS beats in both directions (MSFT Oct 2024 −3.9%, META Feb 2024 +18.1%).
   - The hypothesis: forward capex and cloud-growth guidance carry more of the information. It cannot be
     tested yet, because dated consensus for capex and Azure/Cloud growth exists for only a handful of
     releases (see the PM brief).

## Limits

- Net cash is ignored: it would lower the implied growth for all three.
- Finance-lease capex is excluded from "capex" (XBRL `PaymentsToAcquirePropertyPlantAndEquipment`).
- A single discount rate is used for a decade.
- These are model outputs. They are stored in `expectation_obs` as `market_implied` with grade B, never
  as facts.
