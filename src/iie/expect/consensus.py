"""Dated analyst consensus from archived MarketWatch (FactSet) pages.

Grading:
  B  page captured by the Internet Archive BEFORE the release's EDGAR acceptance: the consensus shown was
     public at the capture time (it may have been revised between capture and release: staleness recorded).
  C  'Estimate' recorded on a page captured AFTER the release (vendor's record of the consensus at report);
     the street-basis 'Actual' from the same block is stored as a reported_result with grade C.
"""
from __future__ import annotations

from datetime import timedelta

from ..expectations import event_key_for
from ..timeutil import parse_utc, to_utc_str
from .archive import fetch_capture, list_captures
from .parsers import parse_marketwatch, parse_marketwatch_old
from .store import put

ALIASES = {"MSFT": ["msft"], "GOOGL": ["googl"], "META": ["fb", "meta"]}
MW = "www.marketwatch.com/investing/stock/{a}/analystestimates"
PROVIDER = "FactSet via MarketWatch (archived)"
BASIS = "street EPS as defined by FactSet contributors (may exclude one-off items; not necessarily GAAP)"


def fiscal_to_mw(key: str) -> str:        # 'FY2024Q2' -> 'Q2 2024'
    return f"{key[-2:]} {key[2:6]}"


def next_quarter(key: str) -> str:
    fy, q = int(key[2:6]), int(key[-1])
    return f"FY{fy + 1}Q1" if q == 4 else f"FY{fy}Q{q + 1}"


def _events(conn, company_id):
    return conn.execute("""SELECT * FROM event WHERE company_id=? AND event_type='earnings_release'
                           AND event_date>='2016-01-01' ORDER BY available_at""", (company_id,)).fetchall()


def _eps_actual(conn, company_id, key, knowledge_cutoff):
    """GAAP diluted EPS for a fiscal quarter as known at a time (XBRL or release), for label validation."""
    from ..derive import FiscalIndex, quarter_value
    from ..pit import load_facts
    idx = FiscalIndex(load_facts(conn, company_id, knowledge_cutoff, "9999-12-31T00:00:00Z"))
    item, _ = quarter_value(idx, "eps_diluted", int(key[2:6]), int(key[-1]))
    return None if item is None else item["value"]


def _split_factor(conn, company_id, after, upto):
    f, notes = 1.0, []
    for s in conn.execute("""SELECT DISTINCT ex_date, ratio FROM corporate_action WHERE company_id=? AND
                             action_type='split' AND provider='yahoo_chart'""", (company_id,)):
        exd = to_utc_str(parse_utc(s["ex_date"] + "T13:30:00Z"))
        if after < exd <= upto:
            f *= s["ratio"]
            notes.append(f"split {s['ratio']:g}:1 ex {s['ex_date']}")
    return f, notes


def ingest(conn, store, company, run_id, stats, max_age_days=150) -> int:
    cid = company["company_id"]
    caps = []
    for a in ALIASES[company["ticker"]]:
        caps += list_captures(store, MW.format(a=a), run_id=run_id)
    caps.sort(key=lambda c: c["captured_at"])
    evs = _events(conn, cid)
    n = 0
    for k, ev in enumerate(evs):
        t_a = ev["available_at"]
        key = event_key_for(conn, ev).split(":")[1]          # e.g. FY2024Q2
        prev_event_at = evs[k - 1]["available_at"] if k else "2000-01-01T00:00:00Z"
        before = [c for c in caps if c["captured_at"] < t_a and
                  c["captured_at"] >= to_utc_str(parse_utc(t_a) - timedelta(days=max_age_days))]
        picks = []
        for lag in (0, 45):
            lim = to_utc_str(parse_utc(t_a) - timedelta(days=lag))
            cand = [c for c in before if c["captured_at"] < lim]
            if cand and cand[-1] not in picks:
                picks.append(cand[-1])
        after = [c for c in caps if t_a < c["captured_at"] <= to_utc_str(parse_utc(t_a) + timedelta(days=120))]
        for cap in picks:
            n += _ingest_pre(conn, store, company, ev, key, cap, prev_event_at, run_id, stats)
        for cap in after[:2]:                       # first capture that carries this quarter in its history
            got = _ingest_post(conn, store, company, ev, key, cap, run_id)
            n += got
            if got:
                break
        conn.commit()                               # per release: keeps the write lock short
    stats[f"consensus_obs_new_{company['ticker']}"] = n
    return n


def _ingest_pre(conn, store, company, ev, key, cap, prev_event_at, run_id, stats) -> int:
    cid, t_a = company["company_id"], ev["available_at"]
    doc_id, body = fetch_capture(store, cap, cid, run_id, f"MarketWatch estimates {company['ticker']} {cap['timestamp']}")
    if body is None:
        return 0
    served_at = cap.get("served_at", cap["captured_at"])
    if served_at >= t_a:
        stats["consensus_redirected_past_release"] = stats.get("consensus_redirected_past_release", 0) + 1
        return 0                                  # the archive served a capture made after the release: never PIT
    if cap.get("served_unverified"):
        stats["consensus_served_capture_unverified"] = stats.get("consensus_served_capture_unverified", 0) + 1
    if served_at != cap["captured_at"]:
        stats["consensus_capture_redirected"] = stats.get("consensus_capture_redirected", 0) + 1
        cap = dict(cap, captured_at=served_at, timestamp=cap["served_timestamp"])
    new, old = parse_marketwatch(body), None
    vals, checks, label_how = None, {}, None
    if new and fiscal_to_mw(key) in new["forward"]:
        f = new["forward"][fiscal_to_mw(key)]
        vals = {"mean": f["average"], "high": f["high"], "low": f["low"]}
        label_how = "explicit quarter label on page"
    else:
        old = parse_marketwatch_old(body)
        if old:
            # 'This Quarter' = first quarter not yet reported at capture time
            reported_before = cap["captured_at"] >= prev_event_at
            if not reported_before:
                stats.setdefault("consensus_skipped_previous_quarter_unreported", 0)
                stats["consensus_skipped_previous_quarter_unreported"] += 1
                return 0
            tq = old["this_quarter"]
            vals = {"mean": tq["mean"], "high": tq["high"], "low": tq["low"], "n_estimates": tq["n"]}
            label_how = "inferred: first unreported quarter at capture time"
            prev_key = None
            fy, q = int(key[2:6]), int(key[-1])
            prev_key = f"FY{fy - 1}Q4" if q == 1 else f"FY{fy}Q{q - 1}"
            act = _eps_actual(conn, cid, prev_key, cap["captured_at"])
            lq = old["last_quarter_eps"]
            checks["last_quarter_eps_page"] = lq
            checks["last_quarter_eps_gaap"] = act
            checks["label_check"] = ("pass" if act is not None and lq is not None and abs(act - lq) <= 0.02 else
                                     "mismatch (street vs GAAP or label)" if act is not None and lq is not None
                                     else "unverifiable")
    if not vals or vals.get("mean") is None:
        stats.setdefault("consensus_capture_unusable", 0)
        stats["consensus_capture_unusable"] += 1
        return 0
    factor, adj = _split_factor(conn, cid, cap["captured_at"], t_a)
    stale = (parse_utc(t_a) - parse_utc(cap["captured_at"])).days
    checks["days_before_release"] = stale
    checks["label"] = label_how
    checks["served_capture"] = "unverified" if cap.get("served_unverified") else "verified"
    n = 0
    for stat, v in vals.items():
        if v is None:
            continue
        val = v / factor if stat != "n_estimates" else v
        n += put(conn, company_id=cid, cls="analyst_consensus", provider=PROVIDER, metric="eps", basis=BASIS,
                 target_period=key, stat=stat, value=val, unit="USD/shares" if stat != "n_estimates" else "count",
                 as_of=cap["captured_at"], as_of_basis="wayback_capture_timestamp", available_at=cap["captured_at"],
                 grade="B", source_doc_id=doc_id, source_url=f"https://web.archive.org/web/{cap['timestamp']}/{cap['original']}",
                 locator="Quarterly Numbers / Earnings Per Share table", method="marketwatch_parser_v1",
                 adjustments=("; ".join(adj) + f"; divided by {factor:g}") if adj else None, checks=checks,
                 natural_key=f"mw|{company['ticker']}|{key}|{stat}|{cap['timestamp']}", run_id=run_id)
    return n


def _ingest_post(conn, store, company, ev, key, cap, run_id) -> int:
    cid = company["company_id"]
    doc_id, body = fetch_capture(store, cap, cid, run_id, f"MarketWatch estimates {company['ticker']} {cap['timestamp']}")
    p = parse_marketwatch(body) if body else None
    if not p or fiscal_to_mw(key) not in p["history"]:
        return 0
    h = p["history"][fiscal_to_mw(key)]
    url = f"https://web.archive.org/web/{cap['timestamp']}/{cap['original']}"
    n = 0
    if h.get("estimate") is not None:
        n += put(conn, company_id=cid, cls="analyst_consensus", provider=PROVIDER, metric="eps", basis=BASIS,
                 target_period=key, stat="mean_at_report", value=h["estimate"], unit="USD/shares",
                 as_of=cap["captured_at"], as_of_basis="wayback_capture_timestamp (captured after the release)",
                 grade="C", source_doc_id=doc_id, source_url=url, locator="Quarterly Numbers: Estimate row",
                 method="marketwatch_parser_v1", notes="vendor's record of the consensus at report; documented after "
                 "the event", natural_key=f"mwh|{company['ticker']}|{key}|estimate|{cap['timestamp']}", run_id=run_id)
    if h.get("actual") is not None:
        n += put(conn, company_id=cid, cls="reported_result", provider=PROVIDER, metric="eps", basis=BASIS,
                 target_period=key, stat="street_actual", value=h["actual"], unit="USD/shares",
                 as_of=cap["captured_at"], as_of_basis="wayback_capture_timestamp (captured after the release)",
                 grade="C", source_doc_id=doc_id, source_url=url, locator="Quarterly Numbers: Actual row",
                 method="marketwatch_parser_v1", notes="street-basis actual as recorded by the vendor",
                 natural_key=f"mwh|{company['ticker']}|{key}|actual|{cap['timestamp']}", run_id=run_id)
    return n
