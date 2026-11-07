"""Market-implied expectations, computed only from information available before each event.

1. Implied earnings move (GOOGL only: the CBOE publishes an equity-VIX for Google, VXGOG, mirrored on FRED;
   there is no free equivalent for MSFT or META). Standard event-variance extraction:
       sigma_event^2 = (IV_pre^2 - IV_base^2) * T + IV_base^2 * T / N
   with T = 30/365 (VXGOG is a 30-day constant-maturity index), N = 21 sessions, IV_pre = close before the
   announcement session, IV_base = close ~35 calendar days earlier (before the release entered the 30-day window).
   Expected absolute move = sqrt(2/pi) * sigma_event. Assumes constant diffusive volatility and no other
   scheduled event in the window: a model, documented as such, not an observation.

2. Implied long-run FCF growth (reverse DCF), all three companies, at any cutoff:
   market value of equity = sum_{t=1..10} FCF0 (1+g)^t / (1+r)^t + TV/(1+r)^10,  TV = FCF10 (1+g_T)/(r - g_T),
   r = 10-year Treasury (point-in-time) + equity risk premium (parameter), g_T = terminal growth (parameter).
   Solved for g by bisection. FCF0 = TTM operating cash flow - capex; equity value = as-traded close x diluted
   weighted-average shares (latest quarter); net cash is ignored (stated). Sensitivity grid over ERP.
   This expresses the price as a growth assumption; it does not forecast and is never calibrated to fit prices.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from ..derive import FiscalIndex, as_traded_close, latest_quarter, ttm
from ..ingest.macro import _insert_if_new, _read_fred_csv
from ..pit import corporate_actions, load_facts, macro_value
from ..timeutil import et_to_utc_str
from .store import put

VX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VXGOGCLS&cosd=2016-01-01"
T, N = 30 / 365, 21
ERP_GRID = (0.04, 0.05, 0.06)
TERMINAL_G = 0.03


def ingest_vxgog(conn, store, run_id, stats) -> int:
    f = store.fetch(VX_URL, source_system="fred", doc_type="fred_csv", run_id=run_id,
                    title="FRED VXGOGCLS (CBOE Equity VIX on Google)")
    n = 0
    for obs_date, val in _read_fred_csv(f.body):
        n += _insert_if_new(conn, dict(
            series_id="CBOE:VXGOG", obs_date=obs_date, value=val, unit="Percent (annualised implied vol)",
            definition="CBOE Equity VIX on Google (30-day implied volatility of GOOG options), via FRED",
            vintage_date=None, available_at=et_to_utc_str(date.fromisoformat(obs_date), 16, 15),
            availability_basis="CBOE index close 16:15 ET (FRED mirror retrieved later)", availability_verified=0,
            source_doc_id=f.doc_id, source_url=f.url, retrieved_at=f.retrieved_at,
            natural_key=f"vxgog|{obs_date}", run_id=run_id))
    stats["vxgog_obs_new"] = n
    return n


def _vx(conn, d, kt):
    return conn.execute("""SELECT * FROM macro_observation WHERE series_id='CBOE:VXGOG' AND obs_date<=? AND
                           value IS NOT NULL AND retrieved_at<=? ORDER BY obs_date DESC, mobs_id DESC LIMIT 1""",
                        (d, kt)).fetchone()


def implied_move(iv_pre, iv_base):
    var = (iv_pre ** 2 - iv_base ** 2) * T + iv_base ** 2 * T / N
    if iv_pre <= iv_base or var <= 0:
        return None
    return math.sqrt(2 / math.pi) * math.sqrt(var)


def ingest_implied_moves(conn, run_id, stats, kt="9999-12-31T00:00:00Z") -> int:
    cid = conn.execute("SELECT company_id FROM company WHERE ticker='GOOGL'").fetchone()[0]
    n = 0
    from ..expectations import event_key_for
    for ev in conn.execute("SELECT * FROM event WHERE company_id=? AND event_type='earnings_release' AND "
                           "event_date>='2016-03-01' ORDER BY available_at", (cid,)).fetchall():
        pre = conn.execute("""SELECT * FROM macro_observation WHERE series_id='CBOE:VXGOG' AND value IS NOT NULL
                              AND available_at<=? AND retrieved_at<=? ORDER BY obs_date DESC LIMIT 1""",
                           (ev["available_at"], kt)).fetchone()          # latest close public before the release
        base = _vx(conn, (date.fromisoformat(pre["obs_date"]) - timedelta(days=35)).isoformat(), kt) if pre else None
        if pre is None or base is None or pre["available_at"] > ev["available_at"]:
            continue
        mv = implied_move(pre["value"] / 100, base["value"] / 100)
        key = event_key_for(conn, ev).split(":")[1]
        n += put(conn, company_id=cid, cls="market_implied", provider="CBOE VXGOG (via FRED)",
                 metric="implied_abs_move_event_day", basis="model: event-variance extraction from 30-day IV",
                 target_period=key, stat="point", value=mv, unit="ratio", as_of=pre["available_at"],
                 as_of_basis="computed from CBOE closes available before the release", available_at=pre["available_at"],
                 grade="B", source_doc_id=pre["source_doc_id"], source_url=pre["source_url"],
                 locator=f"VXGOG {pre['obs_date']}={pre['value']} vs {base['obs_date']}={base['value']}",
                 method="event_variance_v1", checks={"iv_pre": pre["value"], "iv_base": base["value"],
                                                    "pre_date": pre["obs_date"], "base_date": base["obs_date"]},
                 notes=None if mv is not None else "pre-event IV not above baseline: no event premium detectable",
                 natural_key=f"impmove|GOOGL|{ev['accession_no']}", run_id=run_id)
    stats["implied_moves_new"] = n
    return n


# ----------------------------------------------------------------------------------------- reverse DCF
def pv(fcf0, g, r, gt=TERMINAL_G, years=10):
    v, f = 0.0, fcf0
    for t in range(1, years + 1):
        f *= 1 + g
        v += f / (1 + r) ** t
    return v + f * (1 + gt) / (r - gt) / (1 + r) ** years


def solve_growth(target, fcf0, r, gt=TERMINAL_G):
    if fcf0 <= 0 or r <= gt:
        return None
    lo, hi = -0.5, 1.0
    if pv(fcf0, lo, r, gt) > target or pv(fcf0, hi, r, gt) < target:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if pv(fcf0, mid, r, gt) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def implied_growth_at(conn, company, cutoff, kt, fundamentals_cutoff=None):
    """All inputs point-in-time at `cutoff`. With `fundamentals_cutoff`, cash flows and share count are frozen at
    that earlier time while price and rates are taken at `cutoff`: isolates the price-driven revision."""
    cid = company["company_id"]
    idx = FiscalIndex(load_facts(conn, cid, fundamentals_cutoff or cutoff, kt))
    lq = latest_quarter(idx, "revenue")
    if lq is None:
        return {"error": "no quarter visible"}
    fy, q = lq
    cfo, capex = ttm(idx, "operating_cash_flow", fy, q), ttm(idx, "capex", fy, q)
    sh, sh_src = None, None
    for cand in ((fy, f"Q{q}"), (fy, "FY") if q == 4 else None, (fy, f"Q{q - 1}") if q > 1 else (fy - 1, "FY")):
        if cand and sh is None:
            f = idx.get("diluted_shares", cand[0], cand[1])
            if f is not None:
                sh, sh_src = f.as_item(), f"FY{cand[0]}{cand[1]}"
    if sh is None:
        # multi-class issuers (GOOGL) report diluted shares per class (dimensional, absent from companyfacts):
        # derive total diluted shares as net income / diluted EPS for the same period (same EPS for all classes)
        for cand in ((fy, f"Q{q}"), (fy, "FY") if q == 4 else None, (fy, f"Q{q - 1}") if q > 1 else (fy - 1, "FY")):
            if cand and sh is None:
                ni, eps = idx.get("net_income", cand[0], cand[1]), idx.get("eps_diluted", cand[0], cand[1])
                if ni is not None and eps is not None and eps.value:
                    sh = {"value": ni.value / eps.value, "available_at": max(ni.available_at, eps.available_at)}
                    sh_src = f"FY{cand[0]}{cand[1]} net income / diluted EPS (derived)"
    if not (cfo and capex and sh):
        return {"error": "TTM cash flow or share count not available at cutoff"}
    from ..pit import latest_bar
    bar = latest_bar(conn, cid, cutoff, kt)
    y10 = macro_value(conn, "UST:10 Yr", cutoff, kt)
    if bar is None or y10 is None:
        return {"error": "price or 10y yield not available"}
    px = as_traded_close(bar, corporate_actions(conn, cid, kt, "split"))[0]
    from ..derive import split_factor_between
    # share counts published before a later split are rebased to the price date (splits after publication only)
    shares = sh["value"] * split_factor_between(corporate_actions(conn, cid, kt, "split"), sh["available_at"][:10],
                                                bar["trade_date"])
    mcap, fcf0 = px * shares, cfo["value"] - capex["value"]
    rf = y10["value"] / 100
    grid = {f"erp_{int(e * 100)}pct": solve_growth(mcap, fcf0, rf + e) for e in ERP_GRID}
    rev_now = ttm(idx, "revenue", fy, q)
    rev_3y = ttm(idx, "revenue", fy - 3, q)
    fcf_3y = None
    c3, k3 = ttm(idx, "operating_cash_flow", fy - 3, q), ttm(idx, "capex", fy - 3, q)
    if c3 and k3:
        fcf_3y = c3["value"] - k3["value"]
    return {"as_of_quarter": f"FY{fy}Q{q}", "price": px, "price_date": bar["trade_date"], "shares": shares,
            "market_cap": mcap, "fcf_ttm": fcf0, "shares_from": sh_src, "rf_10y": rf, "terminal_growth": TERMINAL_G,
            "implied_fcf_cagr_10y": grid,
            "trailing_3y_revenue_cagr": (rev_now["value"] / rev_3y["value"]) ** (1 / 3) - 1 if rev_now and rev_3y else None,
            "trailing_3y_fcf_cagr": (fcf0 / fcf_3y) ** (1 / 3) - 1 if fcf_3y and fcf_3y > 0 and fcf0 > 0 else None,
            "capex_to_cfo_ttm": capex["value"] / cfo["value"],
            "inputs_available_at": max(x for x in (cfo["available_at"], capex["available_at"], sh["available_at"],
                                                   bar["available_at"], y10["available_at"]))}


def ingest_implied_growth(conn, run_id, stats, kt) -> int:
    """Implied growth just before and just after every earnings release since 2018 (the change across the event
    is the market's revision of long-run expectations; it includes everything else that day)."""
    from ..db import companies
    from ..reaction import PriceSeries, classify_timing
    spy = conn.execute("SELECT company_id FROM company WHERE ticker='SPY'").fetchone()[0]
    cal = PriceSeries(conn, spy, kt).dates
    from ..expectations import event_key_for
    n = 0
    for c in companies(conn):
        for ev in conn.execute("SELECT * FROM event WHERE company_id=? AND event_type='earnings_release' AND "
                               "event_date>='2018-01-01' ORDER BY available_at", (c["company_id"],)).fetchall():
            d0, timing, _ = classify_timing(ev["available_at"], cal)
            if d0 is None or cal.index(d0) + 1 >= len(cal):
                continue
            key = event_key_for(conn, ev).split(":")[1]
            pre_cut = et_to_utc_str(date.fromisoformat(cal[cal.index(d0) - 1]), 16, 1)
            pre_cut = min(pre_cut, ev["available_at"])
            post_cut = et_to_utc_str(date.fromisoformat(d0), 16, 1)
            for tag, cut, fcut in (("pre", pre_cut, None), ("post", post_cut, None),
                                   ("post_same_fundamentals", post_cut, pre_cut)):
                res = implied_growth_at(conn, c, cut, kt, fcut)
                if not res or "error" in res:
                    continue
                for erp, g in res["implied_fcf_cagr_10y"].items():
                    n += put(conn, company_id=c["company_id"], cls="market_implied", provider="reverse DCF (model)",
                             metric=f"implied_fcf_cagr_10y_{erp}", basis=f"10y UST + {erp}, terminal {TERMINAL_G:.0%}",
                             target_period=key, stat=tag, value=g, unit="ratio", as_of=res["inputs_available_at"],
                             as_of_basis="computed from point-in-time inputs", available_at=res["inputs_available_at"],
                             grade="B", method="reverse_dcf_v1", checks=res,
                             notes="model output; ignores net cash; diluted weighted shares as share count",
                             natural_key=f"rdcf|{c['ticker']}|{ev['accession_no']}|{tag}|{erp}", run_id=run_id)
    stats["implied_growth_new"] = n
    return n


def ingest(conn, store, run_id, stats):
    from ..timeutil import utcnow
    ingest_vxgog(conn, store, run_id, stats)
    conn.commit()
    kt = utcnow()
    ingest_implied_moves(conn, run_id, stats, kt)
    ingest_implied_growth(conn, run_id, stats, kt)
