"""Forward consensus snapshots: today's published consensus, stored with today's retrieval time.

Free sources do not provide historical consensus with as-of dates, but a snapshot taken now IS a
point-in-time observation for every future release. Run regularly (e.g. weekly) to build a revision history.
Source: Nasdaq.com analyst pages (Zacks consensus). Grade B: dated by our own retrieval.
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, datetime

from ..fiscal import fiscal_label
from .store import put

FORECAST = "https://api.nasdaq.com/api/analyst/{t}/earnings-forecast"
PROVIDER = "Zacks via Nasdaq.com (live snapshot)"


def _period_end(label: str) -> date:          # 'Dec 2026' -> 2026-12-31
    d = datetime.strptime(label.strip(), "%b %Y")
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def snapshot(conn, store, company, run_id, stats) -> int:
    f = store.fetch(FORECAST.format(t=company["ticker"]), source_system="nasdaq_api", doc_type="consensus_json",
                    run_id=run_id, company_id=company["company_id"], title=f"Nasdaq earnings forecast {company['ticker']}")
    data = json.loads(f.body).get("data") or {}
    fye = company["fiscal_year_end"]
    n = 0
    for block, kind in (("quarterlyForecast", "Q"), ("yearlyForecast", "FY")):
        for r in ((data.get(block) or {}).get("rows") or []):
            pe = _period_end(r["fiscalEnd"])
            if kind == "Q":
                fy, fp = fiscal_label(_shift(pe), pe, fye)
                target = f"FY{fy}{fp}"
            else:
                target = f"FY{fiscal_label(None, pe, fye)[0]}"
            for stat, key in (("mean", "consensusEPSForecast"), ("high", "highEPSForecast"),
                              ("low", "lowEPSForecast"), ("n_estimates", "noOfEstimates")):
                v = r.get(key)
                if v in (None, "", "N/A"):
                    continue
                n += put(conn, company_id=company["company_id"], cls="analyst_consensus", provider=PROVIDER,
                         metric="eps", basis="Zacks consensus (street basis)", target_period=target, stat=stat,
                         value=float(v), unit="count" if stat == "n_estimates" else "USD/shares", as_of=f.retrieved_at,
                         as_of_basis="our retrieval time", available_at=f.retrieved_at, grade="B",
                         source_doc_id=f.doc_id, source_url=f.url, locator=f"{block} {r['fiscalEnd']}",
                         method="nasdaq_forecast_v1",
                         natural_key=f"nasfwd|{company['ticker']}|{target}|{stat}|{f.retrieved_at}", run_id=run_id)
    stats[f"forward_snapshot_{company['ticker']}"] = n
    return n


def _shift(pe: date) -> date:
    m = pe.month - 2
    y = pe.year
    if m <= 0:
        m, y = m + 12, y - 1
    return date(y, m, 1)
