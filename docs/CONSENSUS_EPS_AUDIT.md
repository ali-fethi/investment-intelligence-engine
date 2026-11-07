# Consensus / EPS comparability audit (Sprint 2.1)

Reproduce with `iie expect-quality`. Outputs: `docs/expectations/quality.md`, and `quality.json` with one
record per release. Statistics are seeded and reproducible.

## 1. Reconciliation: 133 releases → 116 with consensus → 86 comparable with a measured reaction

| Step | Releases | Note |
|---|---:|---|
| Earnings releases, MSFT / GOOGL / META, 2016–2026 | 133 | |
| With a dated pre-release consensus EPS (A/B, ≤150 days old) | 116 | 17 have none: no capture or preview found. Not filled |
| Age of the consensus at the release | 0–7 days: 86; 8–30: 16; 31–60: 9; 61–150: 5 | Ages over 30 days are flagged stale |
| …with the vendor's street-basis actual EPS | 91 | 43 from post-release "history" blocks (2020+); **48 recovered in this sprint** from older pages ("Last Quarter's Earnings", captured after the release) |
| …street actual = GAAP actual (within 1 cent) | 64 | GAAP surprise also valid |
| …street actual ≠ GAAP actual | 22 | GAAP surprise **not** comparable (see §2) |
| …no street actual: GAAP vs consensus, basis unverified ("G?") | 25 | Kept apart |
| Comparable ("S", street vs street) with a measured reaction | **86** | Primary sample |

The Sprint 2 report gave n = 43 for the comparable sample. The difference is the 48 street actuals
recovered from older pages.

## 2. GAAP vs street EPS: the 22 releases where they differ

Each difference has an identifiable cause in the filings or releases:

- **Microsoft FY2016–17:** non-GAAP figures excluding Windows 10 revenue deferral and restructuring
  (−$0.04 to −$0.30).
- **Microsoft FY2018 Q2:** $13.8bn tax-reform charge (GAAP −$0.82 vs street $0.96).
- **Microsoft FY2022 Q1:** tax benefit (+$0.44).
- **Microsoft FY2026 Q1:** OpenAI investment impact (the release reports an "as adjusted" figure; −$0.41).
- **Alphabet 2016:** street excludes stock-based compensation (−$1.4 to −$1.8 pre-split).
- **Alphabet Q2 2018:** EU fine (GAAP $4.54 vs street $11.75).
- **Alphabet Q4 2021:** equity-security gains (+$2.70).
- **Facebook 2016–17:** non-GAAP excluding stock-based compensation; Q4 2017 tax-reform charge.

**Implication:** a surprise computed as GAAP EPS against a street consensus is wrong in these quarters.
It is never used in the primary sample.

## 3. Data issues found per release

| Issue | Releases | Handling |
|---|---:|---|
| Stale consensus (captured >30 days before the release) | 14 | Sensitivity samples ≤7 and ≤30 days |
| Pre-release captures for the same quarter disagree by >3% (any age, mostly revisions) | 12 | Latest capture used; revision path stored |
| Mixed provider (Zacks consensus vs FactSet street actual) | 15 | FactSet-only sensitivity |
| Gross outlier (actual/consensus outside [0.5, 2]) | 2 | Kept; trimmed and winsorised sensitivities |
| Archive served a later capture than requested (look-ahead) | 2 | **Excluded** (see `INDEPENDENT_VERIFICATION.md`) |
| Archive served an earlier capture than requested | 10 | Re-dated to the capture actually served |

## 4. Surprise vs market-model abnormal return on d0 (recomputed)

Each test uses: Spearman ρ; a 2,000-draw bootstrap 95% interval, also clustered by reaction week (releases
of different companies in the same week are not independent); and a 5,000-draw permutation p-value.

| Sample | n | ρ | 95% CI | 95% CI (clustered) | Same sign | Permutation p |
|---|---:|---:|---|---|---:|---:|
| **S: street vs street (primary)** | 86 | **+0.33** | [+0.10, +0.52] | [+0.12, +0.52] | 66% | 0.002 |
| S, consensus ≤7 days old | 62 | +0.24 | [−0.03, +0.48] | [−0.02, +0.48] | 63% | 0.056 |
| S, consensus ≤30 days old | 73 | +0.24 | [−0.00, +0.46] | [−0.01, +0.48] | 64% | 0.050 |
| S, FactSet consensus only (same provider as the actual) | 71 | +0.38 | [+0.15, +0.57] | [+0.13, +0.58] | 68% | 0.002 |
| S, excluding releases with a macro release on d0 | 73 | +0.42 | [+0.18, +0.61] | [+0.20, +0.60] | 71% | <0.001 |
| S, excluding the top 5% of \|CAR\| | 80 | +0.34 | [+0.10, +0.55] | [+0.12, +0.53] | 66% | 0.003 |
| S, winsorised 5/95 | 86 | +0.33 | [+0.11, +0.52] | — | 66% | 0.002 |
| G?: GAAP vs consensus, basis unverified | 25 | +0.08 | [−0.37, +0.54] | [−0.37, +0.53] | 52% | 0.69 |
| S ∪ G? | 111 | +0.28 | [+0.08, +0.46] | [+0.10, +0.46] | 63% | 0.003 |
| S, GOOGL / META / MSFT separately | 32 / 28 / 26 | +0.36 / +0.33 / +0.28 | all include 0 | | 81% / 64% / 50% | 0.04 / 0.09 / 0.17 |

## 5. What can and cannot be concluded

- **Supported:** across 86 comparable releases, larger street EPS surprises go with larger abnormal
  returns on the reaction day. The association is moderate (ρ ≈ 0.3), and it survives clustering, trimming
  and winsorising.
- **Fragile:**
  - With consensus captured within 7 days of the release, ρ falls to 0.24 and is not significant at 5%.
    Part of the full-sample association may come from stale consensus. A stale estimate makes the
    "surprise" partly a measure of information already public before the release, which is the
    opposite of what a clean test needs.
  - Per-company samples are too small to distinguish from zero.
- **Multiple looks:** 12 samples are reported; at least one nominal p < 0.05 is likely by chance alone.
- **Not a trading signal:**
  - The surprise is only known after the release, and the abnormal return is in-sample.
  - The market model itself is estimated on pre-event data.
  - Nothing here was tuned or selected on returns.
- **Correction to Sprint 2:** the reported ρ = +0.42 (n = 43, p = 0.066) was a smaller, 2020+-only sample.
  The defensible statement is the table above.
