"""Shared fixtures: an isolated database + evidence store and a synthetic, fully offline source set.

The synthetic company data reuses Alphabet's CIK and fiscal calendar but the numbers are invented
and small, so expected values can be computed by hand in the tests.
"""
from __future__ import annotations

import json

import pytest

from iie.db import connect, init_db
from iie.evidence import EvidenceStore

CIK = "0001652044"


class FakeTransport:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        for key, body in self.routes.items():
            if key in url:
                b = body(url) if callable(body) else body
                return 200, b if isinstance(b, bytes) else b.encode(), "application/octet-stream"
        return 404, b"not found", "text/plain"


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    init_db(conn)
    store = EvidenceStore(conn, root=tmp_path / "evidence")
    yield conn, store, tmp_path
    conn.close()


def _cols(rows):
    keys = ["accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument",
            "isXBRL"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


# accession, filing date, report date, acceptance (UTC), form, items, primary doc
FILINGS = [
    ("0001652044-23-000045", "2023-04-25", "2023-03-31", "2023-04-25T20:30:00.000Z", "10-Q", "", "q1.htm", 1),
    ("0001652044-23-000070", "2023-07-25", "2023-06-30", "2023-07-25T20:30:00.000Z", "10-Q", "", "q2.htm", 1),
    ("0001652044-23-000094", "2023-10-24", "2023-09-30", "2023-10-24T20:30:00.000Z", "10-Q", "", "q3.htm", 1),
    ("0001652044-24-000010", "2024-01-30", "2024-01-30", "2024-01-30T21:01:26.000Z", "8-K", "2.02,9.01", "8k.htm", 1),
    ("0001652044-24-000014", "2024-01-31", "2023-12-31", "2024-01-31T02:43:43.000Z", "10-K", "", "10k.htm", 1),
    ("0001652044-24-000050", "2024-04-25", "2024-03-31", "2024-04-25T20:30:00.000Z", "10-Q", "", "q1_24.htm", 1),
]


def submissions_json():
    return json.dumps({"cik": CIK, "fiscalYearEnd": "1231", "name": "Alphabet Inc.",
                       "filings": {"recent": _cols(FILINGS), "files": []}})


def _f(val, end, accn, form, filed, start=None, fy=2023, fp="FY"):
    d = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


def companyfacts_json():
    A1, A2, A3, AK, AQ = (r[0] for r in (FILINGS[0], FILINGS[1], FILINGS[2], FILINGS[4], FILINGS[5]))
    rev = [
        _f(100e6, "2023-03-31", A1, "10-Q", "2023-04-25", "2023-01-01", fp="Q1"),
        _f(110e6, "2023-06-30", A2, "10-Q", "2023-07-25", "2023-04-01", fp="Q2"),
        _f(210e6, "2023-06-30", A2, "10-Q", "2023-07-25", "2023-01-01", fp="Q2"),
        _f(120e6, "2023-09-30", A3, "10-Q", "2023-10-24", "2023-07-01", fp="Q3"),
        _f(330e6, "2023-09-30", A3, "10-Q", "2023-10-24", "2023-01-01", fp="Q3"),
        _f(460e6, "2023-12-31", AK, "10-K", "2024-01-31", "2023-01-01", fp="FY"),
        # Q1 2023 comparative restated in the Q1 2024 10-Q (100 -> 101)
        _f(101e6, "2023-03-31", AQ, "10-Q", "2024-04-25", "2023-01-01", fy=2024, fp="Q1"),
        _f(140e6, "2024-03-31", AQ, "10-Q", "2024-04-25", "2024-01-01", fy=2024, fp="Q1"),
    ]
    ni = [
        _f(20e6, "2023-03-31", A1, "10-Q", "2023-04-25", "2023-01-01", fp="Q1"),
        _f(22e6, "2023-06-30", A2, "10-Q", "2023-07-25", "2023-04-01", fp="Q2"),
        _f(24e6, "2023-09-30", A3, "10-Q", "2023-10-24", "2023-07-01", fp="Q3"),
        _f(66e6, "2023-09-30", A3, "10-Q", "2023-10-24", "2023-01-01", fp="Q3"),
        _f(96e6, "2023-12-31", AK, "10-K", "2024-01-31", "2023-01-01", fp="FY"),
    ]
    cfo = [
        _f(30e6, "2023-03-31", A1, "10-Q", "2023-04-25", "2023-01-01", fp="Q1"),
        _f(65e6, "2023-06-30", A2, "10-Q", "2023-07-25", "2023-01-01", fp="Q2"),
    ]
    eps = [_f(1.5, "2023-09-30", A3, "10-Q", "2023-10-24", "2023-07-01", fp="Q3"),
           _f(5.0, "2023-12-31", AK, "10-K", "2024-01-31", "2023-01-01", fp="FY")]
    assets = [_f(1000e6, "2023-09-30", A3, "10-Q", "2023-10-24", fp="Q3"),
              _f(1100e6, "2023-12-31", AK, "10-K", "2024-01-31")]
    le = [_f(1000e6, "2023-09-30", A3, "10-Q", "2023-10-24", fp="Q3"),
          _f(1100e6, "2023-12-31", AK, "10-K", "2024-01-31")]
    split = [_f(20, "2022-07-15", A1, "10-Q", "2023-04-25", "2022-07-15", fp="Q1")]

    def concept(units, unit="USD", label="x"):
        return {"label": label, "units": {unit: units}}
    return json.dumps({"cik": int(CIK), "entityName": "Alphabet Inc.", "facts": {"us-gaap": {
        "Revenues": concept(rev, label="Revenues"),
        "NetIncomeLoss": concept(ni, label="Net income"),
        "NetCashProvidedByUsedInOperatingActivities": concept(cfo),
        "EarningsPerShareDiluted": concept(eps, "USD/shares"),
        "Assets": concept(assets), "LiabilitiesAndStockholdersEquity": concept(le),
        "StockholdersEquityNoteStockSplitConversionRatio1": concept(split, "pure"),
    }}})


RELEASE_HTML = """<html><body><p>Alphabet Announces Fourth Quarter 2023 Results</p>
<table>
<tr><td>(In millions, except per share amounts)</td></tr>
<tr><td></td><td>Quarter Ended December 31,</td></tr>
<tr><td></td><td>2022</td><td>2023</td></tr>
<tr><td>Revenues</td><td>$</td><td>95</td><td>$</td><td>130</td></tr>
<tr><td>Income from operations</td><td>18</td><td>31</td></tr>
<tr><td>Net income</td><td>$</td><td>19</td><td>$</td><td>30</td></tr>
<tr><td>Diluted net income per common share</td><td>$</td><td>1.05</td><td>$</td><td>1.64</td></tr>
</table></body></html>"""


def headers_for(url):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for acc, _, _, accept, form, _, prim, _ in FILINGS:
        if acc in url:
            et = datetime.fromisoformat(accept.replace(".000Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
            docs = f"&lt;DOCUMENT&gt;\n&lt;TYPE&gt;{form}\n&lt;FILENAME&gt;{prim}\n&lt;/DOCUMENT&gt;\n"
            if form == "8-K":
                docs += "&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;FILENAME&gt;ex991.htm\n&lt;/DOCUMENT&gt;\n"
            return f"<html><pre>&lt;ACCEPTANCE-DATETIME&gt;{et:%Y%m%d%H%M%S}\n{docs}</pre></html>"
    raise KeyError(url)


PERIODIC_HTML = """<html><body><p>PART I</p><p>Item 1A. Risk Factors</p>
<p>Our business depends on advertising revenue and competition in cloud computing is intense.</p>
<p>Item 7. Management's Discussion and Analysis</p><p>Revenue grew because of strong search demand.</p>
</body></html>"""


def yahoo_chart_json():
    # three bars around the 2022-07-18 20:1 split, vendor-adjusted (post-split basis)
    import datetime as dt
    def ts(d):
        return int(dt.datetime.fromisoformat(d + "T13:30:00+00:00").timestamp())
    days = ["2022-07-14", "2022-07-15", "2022-07-18", "2023-10-24", "2024-01-29", "2024-01-30", "2024-01-31"]
    closes = [111.0, 112.0, 113.0, 125.0, 153.0, 151.5, 140.0]
    return json.dumps({"chart": {"result": [{
        "meta": {"currency": "USD"}, "timestamp": [ts(d) for d in days],
        "events": {"splits": {str(ts("2022-07-18")): {"date": ts("2022-07-18"), "numerator": 20.0,
                                                       "denominator": 1.0}}},
        "indicators": {"quote": [{"open": closes, "high": closes, "low": closes, "close": closes,
                                  "volume": [1000] * len(days)}], "adjclose": [{"adjclose": closes}]}}]}})


TREASURY_CSV = ('Date,"1 Mo","3 Mo","10 Yr","30 Yr"\n'
                "01/31/2024,5.53,5.42,3.99,4.22\n01/30/2024,5.54,5.43,4.06,4.29\n01/29/2024,5.53,5.43,4.08,4.31\n")
FRED_DGS10 = "observation_date,DGS10\n2024-01-26,4.15\n2024-01-29,4.08\n2024-01-30,4.06\n2024-01-31,3.99\n"


def alfred_cpi(vintage):
    base = "observation_date,CPIAUCSL_{v}\n2023-11-01,307.917\n".format(v=vintage.replace("-", ""))
    if vintage >= "2024-01-15":
        return base + "2023-12-01,308.850\n"
    return base


@pytest.fixture
def routes():
    return {
        "submissions/CIK0001652044.json": lambda u: submissions_json(),
        "companyfacts/CIK0001652044.json": lambda u: companyfacts_json(),
        "-index-headers.html": headers_for,
        "ex991.htm": RELEASE_HTML,
        "10k.htm": PERIODIC_HTML, "q1.htm": PERIODIC_HTML, "q2.htm": PERIODIC_HTML, "q3.htm": PERIODIC_HTML,
        "q1_24.htm": PERIODIC_HTML,
        "finance/chart/GOOGL": lambda u: yahoo_chart_json(),
        "daily-treasury-rates.csv/2024": TREASURY_CSV,
        "fredgraph.csv?id=DGS10": FRED_DGS10,
        "alfredgraph.csv?id=CPIAUCSL&vintage_date=2023-12-31": lambda u: alfred_cpi("2023-12-31"),
        "alfredgraph.csv?id=CPIAUCSL&vintage_date=2024-01-31": lambda u: alfred_cpi("2024-01-31"),
    }
