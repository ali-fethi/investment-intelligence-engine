"""Static configuration. Everything environment-specific is overridable by env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("IIE_HOME", Path(__file__).resolve().parents[2]))


def _load_dotenv(path: Path) -> dict:
    """Minimal KEY=VALUE reader for the local, git-ignored .env file (no dependency)."""
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


LOCAL = _load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.environ.get("IIE_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("IIE_DB", DATA_DIR / "iie.sqlite"))
EVIDENCE_DIR = DATA_DIR / "evidence"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

# SEC fair-access policy requires a descriptive User-Agent with a real contact. Set SEC_CONTACT_NAME and
# SEC_CONTACT_EMAIL in the local .env (git-ignored). Until then a placeholder is used and a warning printed.
SEC_PLACEHOLDER = "InvestmentIntelligenceEngine/0.3 research (contact not configured) admin@example.org"


def sec_user_agent(env=None, local=None) -> tuple[str, bool]:
    """(user_agent, configured). Precedence: SEC_USER_AGENT env var, then .env contact fields."""
    import re
    env = os.environ if env is None else env
    local = LOCAL if local is None else local
    if env.get("SEC_USER_AGENT"):
        return env["SEC_USER_AGENT"], True
    name, email = local.get("SEC_CONTACT_NAME", "").strip(), local.get("SEC_CONTACT_EMAIL", "").strip()
    if name and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return f"InvestmentIntelligenceEngine/0.3 {name} {email}", True
    return SEC_PLACEHOLDER, False


SEC_USER_AGENT, SEC_CONTACT_CONFIGURED = sec_user_agent()
GENERIC_USER_AGENT = "Mozilla/5.0 (research data pipeline; InvestmentIntelligenceEngine/0.1)"

# Per-host minimum seconds between requests (SEC limit is 10 req/s; stay well below).
RATE_LIMITS = {
    "www.sec.gov": 0.15,
    "data.sec.gov": 0.15,
    "alfred.stlouisfed.org": 0.4,
    "fred.stlouisfed.org": 0.4,
    "home.treasury.gov": 0.5,
    "query1.finance.yahoo.com": 1.0,
    "api.nasdaq.com": 1.0,
    "www.federalreserve.gov": 0.5,
    "web.archive.org": 1.0,
}


@dataclass(frozen=True)
class CompanySpec:
    ticker: str
    cik: str          # 10-digit zero padded
    name: str
    exchange: str


@dataclass(frozen=True)
class InstrumentSpec:
    ticker: str
    name: str
    kind: str          # etf | index


UNIVERSE = [
    CompanySpec("GOOGL", "0001652044", "Alphabet Inc.", "NASDAQ"),
    CompanySpec("MSFT", "0000789019", "Microsoft Corporation", "NASDAQ"),
    CompanySpec("META", "0001326801", "Meta Platforms, Inc.", "NASDAQ"),
]

# Context issuers: only their EDGAR filing index is ingested, to detect overlapping earnings events
# (peer and mega-cap releases that move the same benchmarks on the same evening).
CONTEXT_ISSUERS = [
    CompanySpec("AAPL", "0000320193", "Apple Inc.", "NASDAQ"),
    CompanySpec("AMZN", "0001018724", "Amazon.com, Inc.", "NASDAQ"),
    CompanySpec("NVDA", "0001045810", "NVIDIA Corporation", "NASDAQ"),
    CompanySpec("AMD", "0000002488", "Advanced Micro Devices, Inc.", "NASDAQ"),
    CompanySpec("TSLA", "0001318605", "Tesla, Inc.", "NASDAQ"),
    CompanySpec("NFLX", "0001065280", "Netflix, Inc.", "NASDAQ"),
    CompanySpec("INTC", "0000050863", "Intel Corporation", "NASDAQ"),
    CompanySpec("QCOM", "0000804328", "QUALCOMM Incorporated", "NASDAQ"),
    CompanySpec("ORCL", "0001341439", "Oracle Corporation", "NYSE"),
    CompanySpec("CRM", "0001108524", "Salesforce, Inc.", "NYSE"),
    CompanySpec("SNAP", "0001564408", "Snap Inc.", "NYSE"),
    CompanySpec("PINS", "0001506293", "Pinterest, Inc.", "NYSE"),
]

# Benchmarks (prices only). ETFs give tradable opens/closes and dividends; indices are price-only.
BENCHMARKS = [
    InstrumentSpec("SPY", "SPDR S&P 500 ETF Trust", "etf"),
    InstrumentSpec("QQQ", "Invesco QQQ Trust (Nasdaq-100)", "etf"),
    InstrumentSpec("XLK", "Technology Select Sector SPDR (S&P 500 Information Technology)", "etf"),
    InstrumentSpec("XLC", "Communication Services Select Sector SPDR", "etf"),
    InstrumentSpec("^GSPC", "S&P 500 index (price)", "index"),
    InstrumentSpec("^IXIC", "Nasdaq Composite index (price)", "index"),
]
# GICS sector of each subject and the sector ETF used as benchmark. The subject is itself a large
# weight of its sector ETF (contamination), which the engine reports rather than hides.
SECTOR_BENCHMARK = {
    "MSFT": ("XLK", "Information Technology", "MSFT is a large XLK constituent (weight not tracked); sector-adjusted returns are damped"),
    "GOOGL": ("XLC", "Communication Services", "GOOGL (both classes) and META are large XLC constituents (weights not "
                                               "tracked); sector-adjusted returns are damped. XLC starts 2018-06-19"),
    "META": ("XLC", "Communication Services", "META and GOOGL are large XLC constituents (weights not tracked); "
                                              "sector-adjusted returns are damped. XLC starts 2018-06-19"),
}

# Canonical metrics -> reported XBRL concepts, in priority order.
# accounting basis is "US GAAP as reported in the filer's XBRL"; no harmonisation is applied.
METRICS = {
    "revenue": dict(label="Revenue", period_type="duration", unit="USD", additive=1,
                    concepts=["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:Revenues",
                              "us-gaap:SalesRevenueNet"],
                    description="Total revenues as reported (ASC 606 concept preferred)."),
    "cost_of_revenue": dict(label="Cost of revenue", period_type="duration", unit="USD", additive=1,
                            concepts=["us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold"],
                            description="Cost of revenues as reported."),
    "research_and_development": dict(label="R&D expense", period_type="duration", unit="USD", additive=1,
                                     concepts=["us-gaap:ResearchAndDevelopmentExpense"],
                                     description="Research and development expense."),
    "operating_income": dict(label="Operating income", period_type="duration", unit="USD", additive=1,
                             concepts=["us-gaap:OperatingIncomeLoss"],
                             description="Income from operations (GAAP)."),
    "net_income": dict(label="Net income", period_type="duration", unit="USD", additive=1,
                       concepts=["us-gaap:NetIncomeLoss"],
                       description="Net income attributable to the parent (GAAP)."),
    "eps_diluted": dict(label="Diluted EPS", period_type="duration", unit="USD/shares", additive=0,
                        concepts=["us-gaap:EarningsPerShareDiluted"],
                        description="Diluted EPS as reported. Not additive across quarters."),
    "diluted_shares": dict(label="Weighted avg diluted shares", period_type="duration", unit="shares", additive=0,
                           concepts=["us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"],
                           description="Weighted-average diluted shares for the period."),
    "operating_cash_flow": dict(label="Cash from operations", period_type="duration", unit="USD", additive=1,
                                concepts=["us-gaap:NetCashProvidedByUsedInOperatingActivities"],
                                description="Net cash provided by operating activities."),
    "capex": dict(label="Capital expenditures", period_type="duration", unit="USD", additive=1,
                  concepts=["us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"],
                  description="Purchases of property and equipment (cash flow statement, positive number)."),
    "total_assets": dict(label="Total assets", period_type="instant", unit="USD", additive=0,
                         concepts=["us-gaap:Assets"], description="Total assets."),
    "total_liabilities": dict(label="Total liabilities", period_type="instant", unit="USD", additive=0,
                              concepts=["us-gaap:Liabilities"], description="Total liabilities."),
    "stockholders_equity": dict(label="Stockholders' equity", period_type="instant", unit="USD", additive=0,
                                concepts=["us-gaap:StockholdersEquity"], description="Total stockholders' equity."),
    "liabilities_and_equity": dict(label="Liabilities and equity", period_type="instant", unit="USD", additive=0,
                                   concepts=["us-gaap:LiabilitiesAndStockholdersEquity"],
                                   description="Total liabilities and stockholders' equity."),
    "cash_and_equivalents": dict(label="Cash and equivalents", period_type="instant", unit="USD", additive=0,
                                 concepts=["us-gaap:CashAndCashEquivalentsAtCarryingValue"],
                                 description="Cash and cash equivalents."),
    "shares_outstanding_cover": dict(label="Shares outstanding (cover page)", period_type="instant", unit="shares",
                                     additive=0, concepts=["dei:EntityCommonStockSharesOutstanding"],
                                     description="Shares outstanding as of the cover-page date (all classes, "
                                                 "only when reported without class dimension)."),
}

# Publication lag (business days) assumed for daily FRED series, verified against exact ALFRED vintages
# by QA check `fred_availability_lag`. DFF is a 7-day series whose weekend values appear with Monday's
# release of Friday's rate, i.e. two business days after a Saturday.
FRED_LAG_BUSINESS_DAYS = {"DFF": 2}

# Metrics used only by the valuation module (Sprint 2.1). Kept out of METRICS so that Sprint 1 snapshots, which
# read METRICS, are unchanged. derive.* looks metrics up in ALL_METRICS.
VALUATION_METRICS = {
    "depreciation": dict(label="Depreciation", period_type="duration", unit="USD", additive=1,
                         concepts=["us-gaap:Depreciation", "us-gaap:DepreciationDepletionAndAmortization"],
                         description="Depreciation of property and equipment (cash-flow statement / notes)."),
    "sbc": dict(label="Stock-based compensation", period_type="duration", unit="USD", additive=1,
                concepts=["us-gaap:ShareBasedCompensation", "us-gaap:AllocatedShareBasedCompensationExpense"],
                description="Stock-based compensation (non-cash add-back in operating cash flow)."),
    "pretax_income": dict(label="Income before taxes", period_type="duration", unit="USD", additive=1,
                          concepts=["us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                                    "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
                          description="Income before income taxes."),
    "income_tax": dict(label="Income tax expense", period_type="duration", unit="USD", additive=1,
                       concepts=["us-gaap:IncomeTaxExpenseBenefit"], description="Provision for income taxes."),
    "interest_expense": dict(label="Interest expense", period_type="duration", unit="USD", additive=1,
                             concepts=["us-gaap:InterestExpenseNonoperating", "us-gaap:InterestExpense",
                                       "us-gaap:InterestPaidNet"],
                             description="Interest expense (or interest paid where only that is tagged)."),
    "cash_st_investments": dict(label="Cash and short-term investments", period_type="instant", unit="USD", additive=0,
                                concepts=["us-gaap:CashCashEquivalentsAndShortTermInvestments"],
                                description="Cash, cash equivalents and short-term investments."),
    "marketable_securities_current": dict(label="Marketable securities (current)", period_type="instant", unit="USD",
                                          additive=0, concepts=["us-gaap:MarketableSecuritiesCurrent",
                                                                "us-gaap:AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                                                                "us-gaap:ShortTermInvestments"],
                                          description="Marketable securities classified as current."),
    "debt_current": dict(label="Long-term debt, current", period_type="instant", unit="USD", additive=0,
                         concepts=["us-gaap:LongTermDebtCurrent"], description="Current portion of long-term debt."),
    "debt_noncurrent": dict(label="Long-term debt, noncurrent", period_type="instant", unit="USD", additive=0,
                            concepts=["us-gaap:LongTermDebtNoncurrent"], description="Long-term debt, noncurrent."),
    "commercial_paper": dict(label="Commercial paper", period_type="instant", unit="USD", additive=0,
                             concepts=["us-gaap:CommercialPaper"], description="Commercial paper outstanding."),
    "finance_lease_liability": dict(label="Finance lease liabilities", period_type="instant", unit="USD", additive=0,
                                    concepts=["us-gaap:FinanceLeaseLiability"], description="Finance lease liabilities."),
}
ALL_METRICS = {**METRICS, **VALUATION_METRICS}

# FRED series: daily market series are taken from the latest FRED vintage (not revised in practice);
# revised macro series are taken from ALFRED vintages.
FRED_DAILY_SERIES = {
    "DGS3MO": ("Percent", "Market yield on U.S. Treasury securities at 3-month constant maturity (H.15)"),
    "DGS2": ("Percent", "Market yield on U.S. Treasury securities at 2-year constant maturity (H.15)"),
    "DGS10": ("Percent", "Market yield on U.S. Treasury securities at 10-year constant maturity (H.15)"),
    "DGS30": ("Percent", "Market yield on U.S. Treasury securities at 30-year constant maturity (H.15)"),
    "DFF": ("Percent", "Effective federal funds rate, daily (FRB NY via H.15)"),
    "T10YIE": ("Percent", "10-year breakeven inflation rate"),
}
# Macro data releases tracked as events (release dates = ALFRED vintage dates; time = agency convention)
MACRO_RELEASE_SERIES = {
    "CPIAUCSL": ("cpi_release", "BLS Consumer Price Index", 8, 30),
    "PAYEMS": ("employment_situation_release", "BLS Employment Situation (nonfarm payrolls)", 8, 30),
    "GDP": ("gdp_release", "BEA Gross Domestic Product", 8, 30),
    "PCEPI": ("pce_release", "BEA Personal Income and Outlays (PCE price index)", 8, 30),
}
# Tier 1 macro events make attribution 'high' when they fall in the d0 reaction period; tier 2 'medium'.
MACRO_TIER1 = ("fomc_statement", "cpi_release", "employment_situation_release")
MACRO_TIER2 = ("gdp_release", "pce_release")

ALFRED_REVISED_SERIES = {
    "CPIAUCSL": ("Index 1982-1984=100, SA", "CPI for All Urban Consumers: All items, seasonally adjusted (BLS)"),
    "UNRATE": ("Percent, SA", "Unemployment rate (BLS)"),
    "GDP": ("Billions of USD, SAAR", "Gross domestic product, nominal (BEA)"),
}
