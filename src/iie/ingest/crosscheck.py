"""Independent price sources, used only for validation (never as primary data).

* Nasdaq.com historical quotes: as-traded daily OHLCV for stocks/ETFs (from ~2016 onward).
* FRED SP500 / NASDAQCOM: S&P 500 and Nasdaq Composite closing levels (S&P Dow Jones / Nasdaq via FRED).
"""
from __future__ import annotations

import json
from datetime import date, datetime

from ..timeutil import et_to_utc_str
from .macro import _insert_if_new, _read_fred_csv

NASDAQ_URL = "https://api.nasdaq.com/api/quote/{t}/historical?assetclass={cls}&fromdate={start}&limit=9999"
FRED_INDEX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
NASDAQ_BASIS = "nasdaq.com historical quotes: split-adjusted as of retrieval (verified: GOOGL 2016 ~ $40), not dividend-adjusted"


def _num(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    return float(s) if s not in ("", "N/A", "--") else None


def ingest_nasdaq(conn, store, company, start, run_id, stats) -> int:
    cls = "etf" if company["kind"] == "etf" else "stocks"
    url = NASDAQ_URL.format(t=company["ticker"], cls=cls, start=start)
    f = store.fetch(url, source_system="nasdaq_api", doc_type="price_json", run_id=run_id,
                    company_id=company["company_id"], title=f"Nasdaq historical {company['ticker']}")
    rows = (json.loads(f.body).get("data") or {}).get("tradesTable", {}).get("rows") or []
    out = []
    for r in rows:
        d = datetime.strptime(r["date"], "%m/%d/%Y").date().isoformat()
        close = _num(r["close"])
        if close is None or et_to_utc_str(date.fromisoformat(d), 16, 0, 0) > f.retrieved_at:
            continue
        out.append((company["company_id"], "nasdaq_api", d, _num(r["open"]), _num(r["high"]), _num(r["low"]), close,
                    None, _num(r["volume"]), NASDAQ_BASIS, et_to_utc_str(date.fromisoformat(d), 16, 0, 0),
                    "regular_session_close_16:00_ET_assumed", f.doc_id, f.retrieved_at,
                    f"bar|nasdaq|{company['ticker']}|{d}", run_id))
    before = conn.total_changes
    conn.executemany("""INSERT OR IGNORE INTO price_bar(company_id, provider, trade_date, open, high, low, close,
        adj_close, volume, adjustment_basis, available_at, availability_basis, source_doc_id, retrieved_at,
        natural_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    stats[f"nasdaq_bars_new_{company['ticker']}"] = conn.total_changes - before
    return len(out)


def ingest_fred_indices(conn, store, start, run_id, stats) -> int:
    n = 0
    for sid, desc in (("SP500", "S&P 500 index close (S&P Dow Jones Indices via FRED)"),
                      ("NASDAQCOM", "Nasdaq Composite index close (Nasdaq via FRED)")):
        f = store.fetch(FRED_INDEX_URL.format(sid=sid, start=start), source_system="fred", doc_type="fred_csv",
                        run_id=run_id, title=f"FRED {sid}")
        for obs_date, val in _read_fred_csv(f.body):
            n += _insert_if_new(conn, dict(
                series_id=f"FRED:{sid}", obs_date=obs_date, value=val, unit="Index", definition=desc,
                vintage_date=None, available_at=et_to_utc_str(date.fromisoformat(obs_date), 23, 59, 59),
                availability_basis="cross-check series; same-day close", availability_verified=0,
                source_doc_id=f.doc_id, source_url=f.url, retrieved_at=f.retrieved_at,
                natural_key=f"fred|{sid}|{obs_date}", run_id=run_id))
    stats["fred_index_obs_new"] = n
    return n
