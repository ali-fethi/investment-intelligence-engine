"""Static configuration. Everything environment-specific is overridable by env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("IIE_HOME", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("IIE_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("IIE_DB", DATA_DIR / "iie.sqlite"))
EVIDENCE_DIR = DATA_DIR / "evidence"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

# SEC fair-access policy requires a descriptive User-Agent with a contact address.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "InvestmentIntelligenceEngine/0.1 research (set SEC_USER_AGENT) admin@example.org"
)
GENERIC_USER_AGENT = "Mozilla/5.0 (research data pipeline; InvestmentIntelligenceEngine/0.1)"

# Per-host minimum seconds between requests (SEC limit is 10 req/s; stay well below).
RATE_LIMITS = {
    "www.sec.gov": 0.15,
    "data.sec.gov": 0.15,
    "alfred.stlouisfed.org": 0.4,
    "fred.stlouisfed.org": 0.4,
    "home.treasury.gov": 0.5,
    "query1.finance.yahoo.com": 1.0,
}


@dataclass(frozen=True)
class CompanySpec:
    ticker: str
    cik: str          # 10-digit zero padded
    name: str
    exchange: str


UNIVERSE = [
    CompanySpec("GOOGL", "0001652044", "Alphabet Inc.", "NASDAQ"),
    CompanySpec("MSFT", "0000789019", "Microsoft Corporation", "NASDAQ"),
    CompanySpec("META", "0001326801", "Meta Platforms, Inc.", "NASDAQ"),
]

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
ALFRED_REVISED_SERIES = {
    "CPIAUCSL": ("Index 1982-1984=100, SA", "CPI for All Urban Consumers: All items, seasonally adjusted (BLS)"),
    "UNRATE": ("Percent, SA", "Unemployment rate (BLS)"),
    "GDP": ("Billions of USD, SAAR", "Gross domestic product, nominal (BEA)"),
}
