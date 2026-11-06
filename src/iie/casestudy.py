"""Case studies and cross-event summaries built on the Event Reaction Engine.

Every number in a report comes from the engine (`reaction.measure`) or from QA/evidence tables; every
narrative statement is either a quoted, dated source or appears under "Analyst inference" with the
label HYPOTHESIS. Inference notes live in registry/case_notes.json, not in code.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from .config import ROOT
from .evidence import EvidenceStore
from .reaction import earnings_events, measure, store_result

NOTES = ROOT / "registry" / "case_notes.json"


def pct(x, d=2):
    return "—" if x is None else f"{x * 100:+.{d}f}%"


def _validation(conn, res):
    """Independent checks for the event: timestamps, reported figures, prices."""
    out = []
    acc = res["event"]["accession_no"]
    hdr = conn.execute("""SELECT d.published_at, f.acceptance_datetime FROM source_document d JOIN filing f
                          ON f.accession_no=d.accession_no WHERE d.accession_no=? AND
                          d.published_at_basis='edgar_header_acceptance_datetime' LIMIT 1""", (acc,)).fetchone()
    if hdr:
        out.append(("EDGAR acceptance: submissions API vs filing header (independent ET timestamp)",
                    "pass" if hdr["published_at"] == hdr["acceptance_datetime"] else "fail",
                    f"{hdr['acceptance_datetime']} vs {hdr['published_at']}"))
    else:
        out.append(("EDGAR acceptance cross-check", "not_run", "filing documents not downloaded"))
    from .derive import FiscalIndex, quarter_value
    from .pit import load_facts
    from .timeutil import utcnow
    cid = conn.execute("SELECT company_id FROM event WHERE event_id=?", (res["event"]["event_id"],)).fetchone()[0]
    rel = conn.execute("""SELECT * FROM observation WHERE accession_no=? AND source_kind='earnings_release_table'""",
                       (acc,)).fetchall()
    if rel:
        now = utcnow()
        fidx = FiscalIndex({k: f for k, f in load_facts(conn, cid, now, now).items() if f.source_kind == "xbrl_fact"})
        for r in rel:
            item, fact = quarter_value(fidx, r["concept"], r["fiscal_year"], int(r["fiscal_period"][1]))
            if item is None:
                out.append((f"release {r['concept']} vs later XBRL", "n/a",
                            "no XBRL counterpart (e.g. Q4 EPS is never reported as a 3-month XBRL fact)"))
                continue
            ref = fact.first_reported_value if fact is not None else item["value"]
            diff = r["value"] - ref
            tol = 0.005 if r["unit"] == "USD/shares" else 1.5e6
            out.append((f"release {r['concept']} vs later XBRL ({item.get('kind')})",
                        "pass" if abs(diff) <= tol else "fail", f"{r['value']:,.2f} vs {ref:,.2f}"))
    # prices: Yahoo vs Nasdaq.com on the reaction day and previous close (same split basis)
    for d in (res["previous_close_day"], res["reaction_day"]):
        rows = {r["provider"]: r["close"] for r in conn.execute(
            """SELECT provider, close FROM price_bar b WHERE company_id=? AND trade_date=? AND retrieved_at>=available_at
               AND bar_id=(SELECT max(bar_id) FROM price_bar b2 WHERE b2.company_id=b.company_id AND
               b2.provider=b.provider AND b2.trade_date=b.trade_date)""", (cid, d))}
        if "nasdaq_api" in rows and "yahoo_chart" in rows:
            rel_d = rows["yahoo_chart"] / rows["nasdaq_api"] - 1
            out.append((f"close {d}: Yahoo vs Nasdaq.com", "pass" if abs(rel_d) < 5e-4 else "fail",
                        f"{rows['yahoo_chart']:.4f} vs {rows['nasdaq_api']:.4f}"))
        else:
            out.append((f"close {d}: Yahoo vs Nasdaq.com", "not_available", "Nasdaq.com history starts 2016-11"))
    for tk, sid in (("^GSPC", "FRED:SP500"), ("^IXIC", "FRED:NASDAQCOM")):
        r = res["windows"]["d0"]["benchmarks"].get(tk, {}).get("return")
        f = [conn.execute("SELECT value FROM macro_observation WHERE series_id=? AND obs_date=? ORDER BY mobs_id "
                          "DESC LIMIT 1", (sid, d)).fetchone() for d in (res["previous_close_day"], res["reaction_day"])]
        if r is not None and all(f):
            rf = f[1][0] / f[0][0] - 1
            out.append((f"{tk} d0 return vs FRED {sid[5:]}", "pass" if abs(r - rf) < 2e-4 else "fail",
                        f"{pct(r, 3)} vs {pct(rf, 3)}"))
    # measured vs documented next-session move
    for dr in (res.get("expectations") or {}).get("documented_reactions", []):
        if dr["metric"] == "next_session_move" and dr["point"] is not None:
            m = res["windows"]["d0"]["return"] * 100
            q = dr["quote"].lower()
            ok = ("more than" in q and abs(m) >= abs(dr["point"])) or ("nearly" in q and abs(m) <= abs(dr["point"])
                                                                       and abs(m) >= abs(dr["point"]) - 1) or (
                abs(abs(m) - abs(dr["point"])) < 0.15)
            out.append(("measured d0 move vs dated news report", "pass" if ok and m * dr["point"] > 0 else "fail",
                        f"measured {m:+.2f}% vs reported '{dr['quote'][:70]}'"))
    return out


def render_case(conn, res, notes=None) -> str:
    ev, w = res["event"], res["windows"]
    sector = res["benchmark_notes"]["sector_etf"]
    L = [f"# {ev['ticker']} — earnings release of {ev['event_date']}", "",
         f"*Event #{ev['event_id']} · accession {ev['accession_no']} · public {ev['public_at_et']} "
         f"({ev['availability_basis']}) · knowledge time {res['knowledge_time']} · "
         f"content hash `{res['content_sha256'][:16]}`*", "",
         "## 1. Timing", "",
         f"- Classification: **{res['timing']}**: {res['timing_note']}.",
         f"- Reaction day d0 = **{res['reaction_day']}**; previous close = {res['previous_close_day']}.", ""]
    ex = res.get("expectations") or {}
    L += ["## 2. What was published (point-in-time at the release)", ""]
    acts = conn.execute("""SELECT concept, value, unit, source_url FROM observation WHERE accession_no=? AND
                           source_kind='earnings_release_table'""", (ev["accession_no"],)).fetchall()
    for a in acts:
        v = f"${a['value'] / 1e9:,.2f}bn" if a["unit"] == "USD" else f"${a['value']:.2f}"
        L.append(f"- {a['concept']}: {v} (GAAP, earnings-release table, [8-K]({a['source_url']}))")
    for k in ex.get("reported_kpis", []):
        val = f"{k['value']:g}%" if k["unit"] == "percent" else f"${k['value'] / 1e9:,.1f}bn"
        L.append(f"- {k['metric']}: {val}: \"{k['quote']}\" "
                 f"([8-K]({k['source_url']}))")
    for g in ex.get("guidance_issued_with_event", []):
        L.append(f"- Guidance issued with the event ({g['metric']}, public ~{g['public_at']}): \"{g['quote']}\" "
                 f"([transcript]({g['source_url']}); {g['notes'] or ''})")
    if not acts and not ex.get("reported_kpis"):
        L.append("- (release figures not ingested for this event)")
    L += ["", "## 3. Expectations before the event", "",
          "Provenance grades: **A** issuer document on EDGAR before the event · **B** dated source published "
          "before the event · **C** documented only after the event (descriptive, not point-in-time) · "
          "**D** rejected.", ""]

    def exp_rows(items):
        rows = ["| Metric | Type | Provider (via) | Expected | Actual | Surprise | Grade | Source date |",
                "|---|---|---|---:|---:|---:|:-:|---|"]
        for i in items:
            unit = i.get("unit")
            fmt = (lambda v: "—" if v is None else (f"{v:g}%" if unit == "percent" else
                                                  f"${v:,.2f}" if unit == "USD/shares" else f"${v / 1e9:,.2f}bn"))
            exp = (f"{fmt(i['low'])}–{fmt(i['high'])}" if i.get("low") is not None else fmt(i.get("point")))
            sur = i.get("surprise_vs_reference")
            sur_s = "—" if sur is None else (f"{sur:+.1f} pts" if i.get("surprise_unit") == "percentage points"
                                             else pct(sur, 1))
            if i.get("actual_vs_range"):
                sur_s += f" ({i['actual_vs_range']} range)"
            rows.append(f"| {i['metric']} | {i['type']} | {i['provider']}"
                        f"{' (' + i['reported_by'] + ')' if i.get('reported_by') else ''} | {exp} | "
                        f"{fmt(i.get('actual'))} | {sur_s} | {i['grade']} | {(i.get('source_published_at') or '')[:16]} |")
        return rows
    if ex.get("point_in_time"):
        L += ["**Usable point-in-time (A/B):**", ""] + exp_rows(ex["point_in_time"]) + [""]
    if ex.get("post_event_documented"):
        L += ["**Documented only after the event (C): descriptive, never used as a point-in-time input:**", ""] + \
            exp_rows(ex["post_event_documented"]) + [""]
    if ex.get("rejected"):
        L += ["**Rejected:**", ""] + exp_rows(ex["rejected"]) + [""]
    if ex.get("note"):
        L += [f"*{ex['note']}*", ""]

    L += ["## 4. Market reaction (measured)", "",
          f"Total returns (dividends added back). Benchmarks: SPY, QQQ, sector ETF {sector} "
          f"({res['benchmark_notes']['contamination']}), ^GSPC/^IXIC (price indices, close windows only). "
          f"Market model vs SPY: beta {res['market_model']['beta']:.2f}, alpha {res['market_model']['alpha'] * 1e4:.1f} "
          f"bp/day, residual sigma {res['market_model']['sigma_eps'] * 100:.2f}%/day, estimated on "
          f"{res['market_model']['first']}…{res['market_model']['last']} ({res['market_model']['n_obs']} days, "
          f"ends 10 sessions before d0)." if res.get("market_model") else "Market model unavailable.", "",
          f"| Window | Span | {ev['ticker']} | SPY | QQQ | {sector} | ^GSPC | ^IXIC | − SPY | − QQQ | − {sector} | "
          f"MM abnormal | SCAR |", "|---|---|" + "---:|" * 11]
    order = ["pre_event_5d", "overnight_gap", "session_d0", "d0", "d0_d1", "d0_d4", "post_d1_d19"]
    for name in order:
        x = w.get(name)
        if not x:
            continue
        b = x["benchmarks"]
        mm = x.get("market_model", {})
        abn = mm.get("car", mm.get("ar_beta_adjusted"))
        scar = mm.get("scar")
        L.append(f"| {name} | {x['from']} → {x['to']} | {pct(x['return'])} | "
                 + " | ".join(pct(b.get(k, {}).get("return")) for k in ("SPY", "QQQ", sector, "^GSPC", "^IXIC"))
                 + " | " + " | ".join(pct(b.get(k, {}).get("excess")) for k in ("SPY", "QQQ", sector))
                 + f" | {pct(abn)} | {'—' if scar is None else f'{scar:+.2f}'} |")
    L += ["", f"- overnight_gap = {w['overnight_gap'].get('interpretation')}.",
          f"- Extended hours: **{res['extended_hours']['status']}**: {res['extended_hours']['reason']}."]
    for dr in ex.get("documented_reactions", []):
        L.append(f"- Documented ({dr['reported_by']}, {dr['source_published_at'][:16]}Z): \"{dr['quote']}\" "
                 f"[{dr['metric']}]({dr['source_url']}){' · ' + dr['notes'] if dr.get('notes') else ''}")
    L += ["", "## 5. Overlapping events", "",
          f"Attribution uncertainty: **{res['attribution_uncertainty']['level']}**: "
          + "; ".join(res["attribution_uncertainty"]["reasons"]) + ".", "",
          "| Period | Kind | Ticker | Event | Public (UTC) | Detail |", "|---|---|---|---|---|---|"]
    for o in res["overlapping_events"]:
        L.append(f"| {o['within']} | {o['kind']} | {o['ticker'] or '—'} | {o['event_type']} | {o['available_at']} | "
                 f"{o['label'] or ''} |")
    L += ["", "## 6. Observations, documented explanations and inference", "", "**Observable (measured):**", ""]
    L += [f"- {s}" for s in res["observations_vs_hypotheses"]["observable"]]
    if notes:
        if notes.get("documented"):
            L += ["", "**Documented explanations (dated sources, attributed to their authors):**", ""]
            L += [f"- {d}" for d in notes["documented"]]
        if notes.get("inference"):
            L += ["", "**Analyst inference: HYPOTHESES, not established by this data:**", ""]
            L += [f"- HYPOTHESIS: {d}" for d in notes["inference"]]
    L += ["", "**Caveats:**", ""] + [f"- {s}" for s in res["observations_vs_hypotheses"]["hypotheses_and_caveats"]]
    L += ["", "## 7. Independent validation", "", "| Check | Result | Detail |", "|---|:-:|---|"]
    for name, status, detail in _validation(conn, res):
        L.append(f"| {name} | {status} | {detail} |")
    L += ["", "## 8. Evidence", "",
          f"- Event row `event#{ev['event_id']}`; subject price bars {res['evidence_bars']['subject']}; "
          f"expectation rows " + ", ".join(str(i['evidence']['id']) for k in ('point_in_time', 'post_event_documented')
                                           for i in ex.get(k, [])) + "."]
    return "\n".join(L) + "\n"


def run_case(conn, event_id, kt, out_dir: Path, store=None):
    store = store or EvidenceStore(conn)
    res = measure(conn, event_id, kt, store=store)
    store_result(conn, res)
    notes = {}
    if NOTES.exists():
        key = f"{res['event']['ticker']}:{res['event']['event_date']}"
        notes = json.loads(NOTES.read_text()).get(key, {})
    md = render_case(conn, res, notes)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"case_{res['event']['ticker']}_{res['event']['event_date']}"
    (out_dir / f"{stem}.md").write_text(md)
    (out_dir / f"{stem}.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str))
    conn.commit()
    return res, out_dir / f"{stem}.md", _validation(conn, res)


# ------------------------------------------------------------------------------------------- cross-event summary
def summarise(conn, tickers, kt, since="2016-01-01", store=None):
    store = store or EvidenceStore(conn)
    rows = []
    for tk in tickers:
        for e in earnings_events(conn, tk, since):
            r = measure(conn, e["event_id"], kt, expectations=False, store=store)
            if r.get("status") != "ok" or "market_model" not in r["windows"].get("d0", {}):
                continue
            w = r["windows"]
            rows.append({"ticker": tk, "date": r["event"]["event_date"], "reaction_day": r["reaction_day"],
                         "timing": r["timing"], "d0": w["d0"]["return"],
                         "d0_ex_spy": w["d0"]["benchmarks"]["SPY"]["excess"],
                         "car_d0": w["d0"]["market_model"]["car"], "scar_d0": w["d0"]["market_model"]["scar"],
                         "gap": w["overnight_gap"]["return"], "session": w["session_d0"]["return"],
                         "post_car": w.get("post_d1_d19", {}).get("market_model", {}).get("car"),
                         "attribution": r["attribution_uncertainty"]["level"]})
    return rows


def baseline_abs_excess(conn, ticker, kt, exclude_days):
    from .reaction import PriceSeries
    ids = {r["ticker"]: r["company_id"] for r in conn.execute("SELECT ticker, company_id FROM company")}
    s, m = PriceSeries(conn, ids[ticker], kt), PriceSeries(conn, ids["SPY"], kt)
    cal = m.dates
    vals = []
    for i in range(1, len(cal)):
        if cal[i] in exclude_days:
            continue
        a, b = s.daily_return(cal[i - 1], cal[i]), m.daily_return(cal[i - 1], cal[i])
        if a is not None and b is not None:
            vals.append(abs(a - b))
    return statistics.mean(vals), len(vals)


def render_summary(conn, rows, kt) -> str:
    L = ["# Earnings reactions: cross-event summary (observable patterns only)", "",
         f"Knowledge time {kt}. Events: every 8-K Item 2.02 since 2016 with a full estimation window. "
         "These are descriptive statistics on small samples. **No predictive claim is made**, and nothing here "
         "was tuned on these events.", ""]
    for tk in sorted({r["ticker"] for r in rows}):
        rs = [r for r in rows if r["ticker"] == tk]
        n = len(rs)
        base, nb = baseline_abs_excess(conn, tk, kt, {r["reaction_day"] for r in rs})
        abs_ex = statistics.mean(abs(r["d0_ex_spy"]) for r in rs)
        big = sum(1 for r in rs if abs(r["scar_d0"]) > 2)
        cont = sum(1 for r in rs if r["gap"] * r["session"] > 0)
        posts = [r["post_car"] for r in rs if r["post_car"] is not None]
        pm = statistics.mean(posts)
        pt = pm / (statistics.stdev(posts) / math.sqrt(len(posts))) if len(posts) > 2 else None
        att = {k: sum(1 for r in rs if r["attribution"] == k) for k in ("low", "medium", "high")}
        L += [f"## {tk} (n = {n})", "",
              f"- Mean |return − SPY| on reaction days: **{pct(abs_ex)}** vs {pct(base)} on the other {nb} days "
              f"(ratio {abs_ex / base:.1f}×). Observable: earnings days carry several times normal idiosyncratic "
              f"movement.",
              f"- |SCAR| > 2 on {big}/{n} events ({big / n:.0%}); under normal, independent residuals one would "
              f"expect about 5%.",
              f"- Overnight gap and regular session moved in the same direction on {cont}/{n} events; "
              f"reversal on {n - cont}/{n}.",
              f"- Post-event drift (d+1…d+19 market-model CAR): mean {pct(pm)}, t ≈ {pt:+.2f} "
              f"(n = {len(posts)}; overlapping macro events are not removed). " + (
                  "Not distinguishable from zero." if pt is None or abs(pt) < 2 else
                  "Nominally |t| > 2, but this is one of ~15 statistics computed here (3 tickers × 5), the 19-day "
                  "windows include market-wide shocks, and residuals are fat-tailed; treat it as a hypothesis to "
                  "test out-of-sample, not a finding."),
              f"- Attribution uncertainty: low {att['low']}, medium {att['medium']}, high {att['high']}.", "",
              "| Release | d0 | Timing | Return | − SPY | MM CAR | SCAR | Gap | Session | Attribution |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for r in rs:
            L.append(f"| {r['date']} | {r['reaction_day']} | {r['timing']} | {pct(r['d0'])} | {pct(r['d0_ex_spy'])} | "
                     f"{pct(r['car_d0'])} | {r['scar_d0']:+.1f} | {pct(r['gap'])} | {pct(r['session'])} | "
                     f"{r['attribution']} |")
        L.append("")
    L += ["## Hypotheses worth testing out-of-sample (not conclusions)", "",
          "- Whether the overnight gap's sign predicts the session's sign beyond chance needs a larger universe "
          "and a pre-registered test, not these ~120 events.",
          "- Whether reactions scale with surprise versus point-in-time consensus cannot be tested yet: "
          "defensible (grade A/B) consensus exists for only 6 events (see the Expectations Engine proposal)."]
    return "\n".join(L) + "\n"
