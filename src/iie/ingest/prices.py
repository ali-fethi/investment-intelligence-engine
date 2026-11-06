"""Daily equity prices and corporate actions from Yahoo Finance's chart endpoint.

Limitations (see docs/DATA_SOURCES.md): unofficial endpoint, no redistribution licence, no SLA.
Yahoo's `close` is split-adjusted *as of the retrieval date* and `adjclose` is split+dividend
adjusted, so the vendor history is NOT point-in-time.  We store the vendor values verbatim with
that basis recorded, store splits/dividends separately, and reconstruct as-traded prices in
`iie.derive.as_traded_close`.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from ..timeutil import ET, et_to_utc_str

CHART_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={p1}&period2={p2}"
             "&interval=1d&events=div%2Csplit&includeAdjustedClose=true")
CLOSE_BASIS = "yahoo:close split-adjusted for all splits known at retrieval_time; not dividend-adjusted"


def ingest_prices(conn, store, company, start: str, run_id, stats, end: str | None = None) -> int:
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp()) if end else \
        int(datetime.now(timezone.utc).timestamp())
    url = CHART_URL.format(ticker=company["ticker"], p1=p1, p2=p2)
    f = store.fetch(url, source_system="yahoo_chart", doc_type="price_json", run_id=run_id,
                    company_id=company["company_id"], title=f"Yahoo chart {company['ticker']} daily")
    return load_chart(conn, company, f.doc_id, f.body, f.retrieved_at, run_id, stats)


def _trade_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ET).date().isoformat()


def load_chart(conn, company, doc_id, body: bytes, retrieved_at, run_id, stats) -> int:
    res = json.loads(body)["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or [None] * len(ts)
    rows = []
    for i, t in enumerate(ts):
        if q["close"][i] is None:
            continue
        d = _trade_date(t)
        nk = f"bar|yahoo|{company['ticker']}|{d}"
        rows.append((company["company_id"], "yahoo_chart", d, q["open"][i], q["high"][i], q["low"][i], q["close"][i],
                     adj[i], q["volume"][i], CLOSE_BASIS, et_to_utc_str(date.fromisoformat(d), 16, 0, 0),
                     "regular_session_close_16:00_ET_assumed", doc_id, retrieved_at, nk, run_id))
    before = conn.total_changes
    conn.executemany("""INSERT OR IGNORE INTO price_bar(company_id, provider, trade_date, open, high, low, close,
        adj_close, volume, adjustment_basis, available_at, availability_basis, source_doc_id, retrieved_at,
        natural_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    n_bars = conn.total_changes - before

    ev = res.get("events") or {}
    ca = []
    for s in (ev.get("splits") or {}).values():
        d = _trade_date(s["date"])
        ratio = s["numerator"] / s["denominator"]
        ca.append((company["company_id"], "split", d, ratio, None, None, None,
                   et_to_utc_str(date.fromisoformat(d), 9, 30), "ex_date_open_assumed_announcement_unknown",
                   "yahoo_chart", doc_id, retrieved_at, f"ca|yahoo|{company['ticker']}|split|{d}", run_id))
    for dv in (ev.get("dividends") or {}).values():
        d = _trade_date(dv["date"])
        ca.append((company["company_id"], "cash_dividend", d, None, dv["amount"],
                   "yahoo: per-share amount, split-adjusted as of retrieval", None,
                   et_to_utc_str(date.fromisoformat(d), 9, 30), "ex_date_open_assumed_announcement_unknown",
                   "yahoo_chart", doc_id, retrieved_at, f"ca|yahoo|{company['ticker']}|div|{d}", run_id))
    before = conn.total_changes
    conn.executemany("""INSERT OR IGNORE INTO corporate_action(company_id, action_type, ex_date, ratio, amount,
        amount_basis, announced_at, available_at, availability_basis, provider, source_doc_id, retrieved_at,
        natural_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ca)
    stats[f"bars_new_{company['ticker']}"] = n_bars
    stats[f"corporate_actions_new_{company['ticker']}"] = conn.total_changes - before
    return n_bars
