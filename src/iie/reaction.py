"""Event Reaction Engine (prototype).

Measures how a stock moved around a dated event, relative to benchmarks, and states what else
happened at the same time. It reports observations; it does not explain them.

Definitions (all returns are total returns: dividends are added back on their ex-date open):
  timing      after_close (>= 16:00 ET), pre_market (< 09:30 ET), intraday, or non_trading_day,
              from the event's public timestamp (EDGAR acceptance for filings).
  d0          first regular session in which the market could react.
  windows     pre_event_5d     close(d0-6) -> close(d0-1)
              overnight_gap    close(d0-1) -> open(d0)   = after-hours + pre-market, NOT separable without
                                                           intraday data (see `extended_hours`)
              session_d0       open(d0)    -> close(d0)
              d0               close(d0-1) -> close(d0)
              d0_d1            close(d0-1) -> close(d0+1)
              d0_d4            close(d0-1) -> close(d0+4)
              post_d1_d19      close(d0)   -> close(d0+19)  (drift after the reaction day)
  abnormal    market-adjusted: r - r_benchmark (SPY, QQQ, sector ETF; ^GSPC/^IXIC for close windows)
              market model:   AR_t = r_t - (alpha + beta * r_SPY,t), estimated on d0-260..d0-11 (>= 120 obs);
                              CAR over the window; SCAR = CAR / (sigma_eps * sqrt(n_days)).
  attribution low / medium / high uncertainty from overlapping events (macro releases, other earnings,
              same-company filings, ex-dividend dates) inside the reaction period.
"""
from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left
from datetime import date, time

from . import __version__
from .config import MACRO_TIER1, SECTOR_BENCHMARK
from .timeutil import ET, et_to_utc_str, parse_utc, utcnow

PRIMARY = "yahoo_chart"
CLOSE_WINDOWS = {"pre_event_5d": (-6, -1), "d0": (-1, 0), "d0_d1": (-1, 1), "d0_d4": (-1, 4),
                 "post_d1_d19": (0, 19)}
EST_WINDOW = (-260, -11)
MIN_EST_OBS = 120


# --------------------------------------------------------------------------------------- price data
class PriceSeries:
    """Daily bars of one instrument as known at knowledge_time (latest retrieval per date), with
    dividends on the same (vendor split-adjusted) basis, so returns are consistent across splits."""

    def __init__(self, conn, company_id, knowledge_time, provider=PRIMARY):
        rows = conn.execute(
            """SELECT b.* FROM price_bar b WHERE company_id=? AND provider=? AND retrieved_at<=? AND bar_id = (
                 SELECT bar_id FROM price_bar b2 WHERE b2.company_id=b.company_id AND b2.provider=b.provider
                 AND b2.trade_date=b.trade_date AND b2.retrieved_at<=? AND b2.retrieved_at>=b2.available_at ORDER BY retrieved_at DESC, bar_id DESC LIMIT 1)
               ORDER BY trade_date""", (company_id, provider, knowledge_time, knowledge_time)).fetchall()
        self.bars = {r["trade_date"]: r for r in rows}
        self.dates = [r["trade_date"] for r in rows]
        self.div = {}
        for r in conn.execute("""SELECT ex_date, amount FROM corporate_action WHERE company_id=? AND provider=?
                                 AND action_type='cash_dividend' AND retrieved_at<=?
                                 ORDER BY retrieved_at""", (company_id, provider, knowledge_time)):
            self.div[r["ex_date"]] = r["amount"]

    def has(self, d):
        return d in self.bars

    def point(self, d, kind):
        b = self.bars.get(d)
        return None if b is None else b[kind]

    def segment_factor(self, d_prev, d, kind_end):
        """Factor from close(d_prev) to open(d) or close(d), dividend added at the ex-date open."""
        c0, o, c1 = self.point(d_prev, "close"), self.point(d, "open"), self.point(d, "close")
        if c0 is None or (kind_end == "open" and o is None) or (kind_end == "close" and c1 is None):
            return None
        div = self.div.get(d, 0.0)
        if kind_end == "open":
            return (o + div) / c0
        return (c1 + div) / c0

    def daily_return(self, d_prev, d):
        f = self.segment_factor(d_prev, d, "close")
        return None if f is None else f - 1


def window_return(series: PriceSeries, cal, start, end):
    """start/end = (date, 'open'|'close'); start must be a close or the open of the end date."""
    (sd, sk), (ed, ek) = start, end
    if sk == "open":                          # open(d) -> close(d)
        o, c = series.point(sd, "open"), series.point(ed, "close")
        if sd != ed or o in (None, 0) or c is None:
            return None
        return c / o - 1
    i, j = cal.index(sd), cal.index(ed)
    f = 1.0
    for k in range(i + 1, j + 1):
        kind = ek if k == j else "close"
        seg = series.segment_factor(cal[k - 1], cal[k], kind)
        if seg is None:
            return None
        f *= seg
    return f - 1


# --------------------------------------------------------------------------------------- timing
def classify_timing(public_at_utc: str, cal):
    t = parse_utc(public_at_utc).astimezone(ET)
    d = t.date().isoformat()
    i = bisect_left(cal, d)
    if i >= len(cal):
        return None, None, "beyond_price_history"
    if cal[i] != d:
        return cal[i], "non_trading_day", "next session after a non-trading day"
    if t.time() < time(9, 30):
        return d, "pre_market", "before the open: overnight gap and session both react"
    if t.time() < time(16, 0):
        return d, "intraday", "during the session: only part of d0 follows the event; no clean decomposition"
    if i + 1 >= len(cal):
        return None, "after_close", "next session not yet in price history"
    return cal[i + 1], "after_close", "after the close: after-hours, pre-market and next session react"


# --------------------------------------------------------------------------------------- statistics
def ols(y, x):
    n = len(y)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    alpha = my - beta * mx
    resid = [b - alpha - beta * a for a, b in zip(x, y)]
    sigma = math.sqrt(sum(e * e for e in resid) / (n - 2))
    return alpha, beta, sigma


def market_model(sub: PriceSeries, mkt: PriceSeries, cal, d0_idx):
    lo, hi = d0_idx + EST_WINDOW[0], d0_idx + EST_WINDOW[1]
    ys, xs, used = [], [], []
    for k in range(max(lo, 1), hi + 1):
        rs, rm = sub.daily_return(cal[k - 1], cal[k]), mkt.daily_return(cal[k - 1], cal[k])
        if rs is not None and rm is not None:
            ys.append(rs)
            xs.append(rm)
            used.append(cal[k])
    if len(ys) < MIN_EST_OBS:
        return None
    if len(set(xs)) < 2:
        return None                            # degenerate benchmark series: no model rather than a fake one
    a, b, s = ols(ys, xs)
    return {"alpha": a, "beta": b, "sigma_eps": s, "n_obs": len(ys), "first": used[0], "last": used[-1],
            "benchmark": "SPY"}


# --------------------------------------------------------------------------------------- overlaps
def _reaction_day(cal, public_at):
    return classify_timing(public_at, cal)[0]


def find_overlaps(conn, event, company_id, cal, d0_idx, public_at, knowledge_time, store=None):
    d0 = cal[d0_idx]
    d4 = cal[min(d0_idx + 4, len(cal) - 1)]
    close_d0 = et_to_utc_str(date.fromisoformat(d0), 16, 0)
    close_d4 = et_to_utc_str(date.fromisoformat(d4), 16, 0)
    prev_close = et_to_utc_str(date.fromisoformat(cal[d0_idx - 1]), 16, 0)
    start = min(public_at, prev_close)
    rows = conn.execute(
        """SELECT e.*, c.ticker, c.role FROM event e LEFT JOIN company c USING(company_id)
           JOIN source_document d ON d.doc_id=e.source_doc_id
           WHERE e.available_at > ? AND e.available_at <= ? AND e.event_id != ? AND d.retrieved_at <= ?
           AND (e.company_id IS NULL OR e.event_type IN ('earnings_release','material_event_8k','dividend_ex_date',
                                                         'split_ex_date','periodic_report'))
           ORDER BY e.available_at""", (start, close_d4, event["event_id"], knowledge_time)).fetchall()
    out = []
    for r in rows:
        same = r["company_id"] == company_id
        if r["company_id"] is not None and not same and r["event_type"] != "earnings_release":
            continue
        rd = _reaction_day(cal, r["available_at"]) if r["event_type"] not in ("dividend_ex_date",
                                                                               "split_ex_date") else r["event_date"]
        in_d0 = rd == d0
        if not in_d0 and (rd is None or rd < d0):
            continue
        if same and r["event_type"] == "periodic_report" and r["available_at"] <= close_d0:
            kind = "same_company_companion_filing"     # e.g. 10-Q filed minutes after the release
        elif r["company_id"] is None:
            kind = "macro"
        elif same:
            kind = "same_company"
        else:
            kind = f"{r['role']}_earnings"
        details = json.loads(r["details_json"])
        if r["event_type"] == "fomc_statement":
            from .evidence import EvidenceStore
            from .ingest.macro_events import fomc_decision
            dec, kind_fomc = fomc_decision(conn, store or EvidenceStore(conn), r)
            details["decision"], details["label"] = dec, f"FOMC {kind_fomc}" + (
                f": {dec['action']} {dec['range_text']}%" if dec else "")
        out.append({"event_type": r["event_type"], "ticker": r["ticker"], "kind": kind,
                    "available_at": r["available_at"], "reaction_day": rd,
                    "within": "d0_reaction_period" if in_d0 else "d1_to_d4",
                    "label": details.get("label") or (details.get("decision") and json.dumps(details["decision"]))
                    or ",".join(details.get("items", {}).keys()) or None,
                    "evidence": {"table": "event", "id": r["event_id"]}})
    return out


def attribution_level(overlaps):
    d0 = [o for o in overlaps if o["within"] == "d0_reaction_period"]
    reasons = []
    t1 = [o["event_type"] for o in d0 if o["kind"] == "macro" and o["event_type"] in MACRO_TIER1]
    if t1:
        reasons.append(f"tier-1 macro release inside the d0 reaction period ({', '.join(sorted(set(t1)))})")
    if any(o["kind"] == "same_company" and o["event_type"] in ("material_event_8k",) for o in d0):
        reasons.append("another same-company 8-K inside the d0 reaction period")
    if reasons:
        return "high", reasons
    t2 = [o["event_type"] for o in d0 if o["kind"] == "macro" and o["event_type"] not in MACRO_TIER1]
    if t2:
        reasons.append(f"tier-2 macro release inside the d0 reaction period ({', '.join(sorted(set(t2)))})")
    if any(o["kind"].endswith("_earnings") for o in d0):
        reasons.append("other large-cap earnings react on the same day (benchmarks move with them)")
    if any(o["event_type"] == "dividend_ex_date" and o["ticker"] for o in d0):
        reasons.append("ex-dividend date in d0 (added back in total return)")
    if reasons:
        return "medium", reasons
    return "low", ["no overlapping dated event found in the tracked set (untracked news may still exist)"]


# --------------------------------------------------------------------------------------- main
def measure(conn, event_id: int, knowledge_time: str | None = None, expectations=True, store=None) -> dict:
    kt = knowledge_time or utcnow()
    ev = conn.execute("SELECT e.*, c.ticker, c.company_id cid FROM event e JOIN company c USING(company_id) "
                      "WHERE event_id=?", (event_id,)).fetchone()
    tk, cid = ev["ticker"], ev["cid"]
    bench_ids = {r["ticker"]: r["company_id"] for r in conn.execute(
        "SELECT ticker, company_id FROM company WHERE role='benchmark'")}
    spy = PriceSeries(conn, bench_ids["SPY"], kt)
    cal = spy.dates
    sub = PriceSeries(conn, cid, kt)
    public_at = ev["available_at"]
    d0, timing, timing_note = classify_timing(public_at, cal)
    public_date = parse_utc(ev["available_at"]).astimezone(ET).date().isoformat()
    res = {"event": {"event_id": event_id, "ticker": tk, "type": ev["event_type"],
                     "event_date": public_date if ev["accession_no"] else ev["event_date"],
                     "recorded_event_date": ev["event_date"],
                     "public_at_utc": public_at, "public_at_et": parse_utc(public_at).astimezone(ET).isoformat(),
                     "availability_basis": ev["availability_basis"], "accession_no": ev["accession_no"],
                     "evidence": {"table": "event", "id": event_id}},
           "timing": timing, "timing_note": timing_note, "knowledge_time": kt, "code_version": __version__}
    if d0 is None or cal.index(d0) + 4 >= len(cal) or not sub.has(d0):
        res["status"] = "insufficient_price_history"
        return res
    i0 = cal.index(d0)
    res["reaction_day"] = d0
    res["previous_close_day"] = cal[i0 - 1]

    sector = SECTOR_BENCHMARK.get(tk)
    bmarks = {"SPY": spy, "QQQ": PriceSeries(conn, bench_ids["QQQ"], kt),
              "^GSPC": PriceSeries(conn, bench_ids["^GSPC"], kt), "^IXIC": PriceSeries(conn, bench_ids["^IXIC"], kt)}
    if sector:
        bmarks[sector[0]] = PriceSeries(conn, bench_ids[sector[0]], kt)
    windows = {}
    for name, (a, b) in CLOSE_WINDOWS.items():
        if i0 + b >= len(cal) or i0 + a < 0:
            continue
        windows[name] = ((cal[i0 + a], "close"), (cal[i0 + b], "close"), b - a)
    windows["overnight_gap"] = ((cal[i0 - 1], "close"), (d0, "open"), None)
    windows["session_d0"] = ((d0, "open"), (d0, "close"), None)

    mm = market_model(sub, spy, cal, i0)
    out = {}
    for name, (s, e, ndays) in windows.items():
        r = window_return(sub, cal, s, e)
        w = {"from": f"{s[1]} {s[0]}", "to": f"{e[1]} {e[0]}", "return": r, "benchmarks": {}}
        for bn, bs in bmarks.items():
            if bn.startswith("^") and (s[1] == "open" or e[1] == "open"):
                continue                       # index opens are not tradable prints; use ETFs for open windows
            rb = window_return(bs, cal, s, e)
            w["benchmarks"][bn] = {"return": rb, "excess": (r - rb) if (r is not None and rb is not None) else None}
        if mm and r is not None and w["benchmarks"].get("SPY", {}).get("return") is not None:
            if ndays:
                car = 0.0
                ok = True
                for k in range(cal.index(s[0]) + 1, cal.index(e[0]) + 1):
                    rs, rm = sub.daily_return(cal[k - 1], cal[k]), spy.daily_return(cal[k - 1], cal[k])
                    if rs is None or rm is None:
                        ok = False
                        break
                    car += rs - mm["alpha"] - mm["beta"] * rm
                if ok:
                    sd = mm["sigma_eps"] * math.sqrt(ndays)
                    w["market_model"] = {"car": car, "scar": car / sd if sd > 0 else None, "n_days": ndays}
            else:
                ar = r - mm["beta"] * w["benchmarks"]["SPY"]["return"]
                w["market_model"] = {"ar_beta_adjusted": ar, "note": "partial-day window: beta-adjusted only, no z"}
        out[name] = w
    out["overnight_gap"]["interpretation"] = {
        "after_close": "after-hours (16:00-20:00 ET on the announcement day) + pre-market (04:00-09:30 ET on d0); "
                       "not separable without intraday data",
        "pre_market": "pre-market reaction plus the prior evening; not separable without intraday data",
        "intraday": "pre-announcement overnight move (not a reaction to this event)",
        "non_trading_day": "everything between the last close and the next open, including the event"}.get(timing)
    res["windows"] = out
    res["extended_hours"] = {"status": "not_measured",
                             "reason": "no licensed intraday/extended-hours history in this system; the overnight "
                                       "gap is the measured upper level of detail"}
    res["market_model"] = mm
    res["benchmark_notes"] = {"sector_etf": sector[0] if sector else None,
                              "sector": sector[1] if sector else None,
                              "contamination": sector[2] if sector else None,
                              "^GSPC/^IXIC": "price indices (no dividends); used for close-to-close windows only"}

    ov = find_overlaps(conn, ev, cid, cal, i0, public_at, kt, store)
    level, reasons = attribution_level(ov)
    res["overlapping_events"] = ov
    res["attribution_uncertainty"] = {"level": level, "reasons": reasons}
    if expectations:
        from .expectations import expectations_for_event
        res["expectations"] = expectations_for_event(conn, ev, public_at, kt)
    res["evidence_bars"] = {"subject": [sub.bars[cal[k]]["bar_id"] for k in range(i0 - 1, min(i0 + 5, len(cal)))
                                        if cal[k] in sub.bars]}
    res["data_through"] = cal[min(i0 + 19, len(cal) - 1)]
    res["observations_vs_hypotheses"] = describe(res)
    res["status"] = "ok"
    res["content_sha256"] = hashlib.sha256(json.dumps(res, sort_keys=True, default=str).encode()).hexdigest()
    return res


def describe(res) -> dict:
    """Plain statements, split into what the data show and what would need further evidence."""
    w = res["windows"]
    obs, hyp = [], []

    def pct(x):
        return "n/a" if x is None else f"{x * 100:+.2f}%"
    d0 = w.get("d0", {})
    obs.append(f"{res['event']['ticker']} {pct(d0.get('return'))} on {res['reaction_day']} (close-to-close); "
               f"SPY {pct(d0.get('benchmarks', {}).get('SPY', {}).get('return'))}, "
               f"QQQ {pct(d0.get('benchmarks', {}).get('QQQ', {}).get('return'))}.")
    if d0.get("market_model", {}).get("scar") is not None:
        obs.append(f"Market-model abnormal return {pct(d0['market_model']['car'])} "
                   f"(standardised {d0['market_model']['scar']:+.1f} sigma of pre-event idiosyncratic noise).")
    og, sd = w.get("overnight_gap", {}).get("return"), w.get("session_d0", {}).get("return")
    if og is not None and sd is not None:
        obs.append(f"Overnight gap {pct(og)}, then regular session {pct(sd)} "
                   f"({'continuation' if og * sd > 0 else 'reversal'} intraday).")
    au = res["attribution_uncertainty"]
    if au["level"] != "low":
        hyp.append(f"Attribution uncertainty {au['level']}: " + "; ".join(au["reasons"]) +
                   ". The abnormal return cannot be attributed to the earnings release alone without intraday data.")
    hyp.append("Any statement that the move was caused by a specific item (guidance, segment growth, capex) is a "
               "hypothesis unless it is documented by a dated source; see expectations and documented_explanations.")
    return {"observable": obs, "hypotheses_and_caveats": hyp}


def store_result(conn, res) -> int:
    if res.get("status") != "ok":
        return 0
    d0 = res["windows"].get("d0", {})
    cur = conn.execute(
        """INSERT INTO event_reaction(event_id, company_id, reaction_day, timing, car_d0_mm, attribution, result_json,
             data_through, knowledge_time, computed_at, code_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (res["event"]["event_id"], conn.execute("SELECT company_id FROM event WHERE event_id=?",
                                                (res["event"]["event_id"],)).fetchone()[0],
         res["reaction_day"], res["timing"], d0.get("market_model", {}).get("car"),
         res["attribution_uncertainty"]["level"], json.dumps(res, sort_keys=True, default=str),
         res["data_through"], res["knowledge_time"], utcnow(), __version__))
    return cur.lastrowid


def earnings_events(conn, ticker, since="2016-01-01"):
    return conn.execute("""SELECT e.event_id FROM event e JOIN company c USING(company_id)
                           WHERE c.ticker=? AND e.event_type='earnings_release' AND e.event_date>=?
                           ORDER BY e.available_at""", (ticker, since)).fetchall()
