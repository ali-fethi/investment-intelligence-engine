"""Live-endpoint checks (opt-in: IIE_NETWORK_TESTS=1). They verify that the provider formats and the
timestamp conventions this code relies on still hold."""
import json
import os

import pytest

from iie.evidence import Transport
from iie.ingest.documents import parse_index_headers
from iie.ingest.sec import SUBMISSIONS_URL, archive_url
from iie.timeutil import edgar_acceptance_to_utc

pytestmark = [pytest.mark.network,
              pytest.mark.skipif(os.environ.get("IIE_NETWORK_TESTS") != "1", reason="set IIE_NETWORK_TESTS=1")]


def test_edgar_acceptance_is_utc_in_submissions_api():
    t = Transport()
    status, body, _ = t.get(SUBMISSIONS_URL.format(cik="0000789019"))
    assert status == 200
    r = json.loads(body)["filings"]["recent"]
    i = r["form"].index("10-Q")
    acc = r["accessionNumber"][i]
    status, hdr, _ = t.get(archive_url("0000789019", acc, f"{acc}-index-headers.html"))
    header_accept, docs = parse_index_headers(hdr.decode("utf-8", "replace"))
    assert header_accept == edgar_acceptance_to_utc(r["acceptanceDateTime"][i])
    assert any(typ == "10-Q" for typ, _ in docs)


def test_yahoo_close_is_split_adjusted():
    # GOOGL traded ~$2,250 before the 2022-07-18 20:1 split; the vendor reports ~$112
    t = Transport()
    status, body, _ = t.get("https://query1.finance.yahoo.com/v8/finance/chart/GOOGL?period1=1657843200"
                            "&period2=1657929600&interval=1d&events=split")
    assert status == 200
    close = json.loads(body)["chart"]["result"][0]["indicators"]["quote"][0]["close"][0]
    assert 90 < close < 140


def test_alfred_vintage_header():
    t = Transport()
    status, body, _ = t.get("https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=CPIAUCSL"
                            "&vintage_date=2024-01-05&cosd=2023-10-01")
    assert status == 200 and body.decode().splitlines()[0] == "observation_date,CPIAUCSL_20240105"
