"""Join expectations to outcomes and to the Event Reaction Engine, one row per earnings release.

For each release (public time t):
  * expectations usable at t (grade A/B, available_at < t), by class: guidance, consensus, market-implied;
  * consensus revision path (earliest vs latest usable capture within 150 days);
  * outcomes: GAAP actuals from the release (grade A) and the vendor's street-basis actual (grade C);
  * surprises on a consistent basis (street vs street, GAAP vs guided GAAP ranges);
  * the measured reaction and the confounding events from the reaction engine.
Nothing here is fitted. Summary statistics are descriptive, with sample sizes.
"""
from __future__ import annotations

import math
import statistics

from ..expectations import event_key_for
from ..reaction import measure
from .store import put

CLAIM_CLASS = {"management_guidance": "management_guidance", "no_guidance_policy": "management_guidance",
               "sell_side_consensus": "analyst_consensus", "reported_kpi": "reported_result",
               "guidance_issued": "management_guidance", "documented_reaction": "narrative"}
METRIC_MAP = {"eps_diluted": "eps", "intelligent_cloud_revenue": "segment_revenue_intelligent_cloud",
              "productivity_business_processes_revenue": "segment_revenue_pbp",
              "more_personal_computing_revenue": "segment_revenue_mpc"}


def _next_q(key):
    fy, q = int(key[2:6]), int(key[-1])
    return f"FY{fy + 1}Q1" if q == 4 else f"FY{fy}Q{q + 1}"


def import_registry(conn, run_id, stats) -> int:
    """Curated, quote-verified claims (Sprint 'expectation' table) mapped into the five classes."""
    n = 0
    rows = conn.execute("""SELECT * FROM expectation e WHERE expectation_id=(SELECT max(expectation_id) FROM expectation
                           e2 WHERE e2.natural_key=e.natural_key)""").fetchall()
    for r in rows:
        cls = CLAIM_CLASS.get(r["expectation_type"])
        if cls is None:
            continue
        key = r["event_key"].split(":")[1]
        metric = r["metric"]
        target = key
        if r["expectation_type"] == "guidance_issued":
            metric, target = metric.replace("next_q_", ""), _next_q(key)
        metric = METRIC_MAP.get(metric, metric)
        if r["expectation_type"] == "no_guidance_policy":
            metric = "quantitative_guidance"
            vals = [("text", None)]
        basis = "consensus as reported by the source" if cls == "analyst_consensus" else None
        if r["expectation_type"] != "no_guidance_policy":
            vals = [(s, v) for s, v in (("low", r["value_low"]), ("high", r["value_high"]),
                                        ("mean", r["value_point"])) if v is not None] or [("text", None)]
        for stat, v in vals:
            if cls != "analyst_consensus" and stat == "mean":
                stat = "point"
            n += put(conn, company_id=r["company_id"], cls=cls, provider=r["provider"], metric=metric, basis=basis,
                     target_period=target, stat=stat, value=v,
                     value_text=("none in release" if r["expectation_type"] == "no_guidance_policy" else
                                 r["verbatim_quote"]) if stat == "text" else None, unit=r["unit"],
                     as_of=r["source_published_at"] or r["retrieved_at"], as_of_basis=r["published_at_basis"],
                     available_at=r["available_at"], grade=r["provenance_grade"], source_doc_id=r["source_doc_id"],
                     source_url=r["source_url"], locator=r["verbatim_quote"], method="registry_import_v1",
                     notes=r["notes"], natural_key=f"reg|{r['natural_key']}|{stat}", run_id=run_id)
    stats["registry_imported"] = n
    return n


def ingest_reported_results(conn, company, run_id, stats):
    """Reported results are read directly from `observation` (grade A); nothing to copy."""
    return 0


# ------------------------------------------------------------------------------------------------ event table
def _latest(conn, cid, cls, metric, target, t, kt, stat, provider=None):
    sql = """SELECT * FROM expectation_obs WHERE company_id=? AND class=? AND metric=? AND target_period=? AND stat=?
             AND available_at IS NOT NULL AND available_at<? AND created_at<=?"""
    p = [cid, cls, metric, target, stat, t, kt]
    if provider:
        sql += " AND provider LIKE ?"
        p.append(provider)
    return conn.execute(sql + " ORDER BY as_of DESC, eo_id DESC", p).fetchall()


def _obs(conn, acc, concept):
    r = conn.execute("""SELECT value FROM observation WHERE accession_no=? AND source_kind='earnings_release_table'
                        AND concept=?""", (acc, concept)).fetchone()
    return None if r is None else r["value"]


def _xbrl_first(conn, cid, key, metric):
    from ..derive import FiscalIndex, quarter_value
    from ..pit import load_facts
    idx = FiscalIndex({k: f for k, f in load_facts(conn, cid, "9999-12-31T00:00:00Z", "9999-12-31T00:00:00Z").items()
                       if f.source_kind == "xbrl_fact"})
    item, fact = quarter_value(idx, metric, int(key[2:6]), int(key[-1]))
    if item is None:
        return None
    return fact.first_reported_value if fact is not None else item["value"]


def event_row(conn, ev, kt, store=None):
    cid = ev["company_id"]
    tk = conn.execute("SELECT ticker FROM company WHERE company_id=?", (cid,)).fetchone()[0]
    key = event_key_for(conn, ev).split(":")[1]
    t = ev["available_at"]
    row = {"ticker": tk, "event_id": ev["event_id"], "accession_no": ev["accession_no"], "quarter": key,
           "public_at": t}
    # consensus EPS (street basis) and its revision path
    means = _latest(conn, cid, "analyst_consensus", "eps", key, t, kt, "mean")
    if means:
        last, first = means[0], means[-1]
        row["consensus_eps"] = last["value"]
        row["consensus_eps_provider"] = last["provider"]
        row["consensus_eps_grade"] = last["provenance_grade"]
        from ..timeutil import parse_utc
        row["consensus_eps_days_before"] = (parse_utc(t) - parse_utc(last["as_of"])).days
        row["consensus_eps_as_of"] = last["as_of"]
        row["consensus_eps_evidence"] = last["eo_id"]
        if first["eo_id"] != last["eo_id"] and first["value"]:
            row["consensus_eps_revision"] = last["value"] / first["value"] - 1
            row["consensus_eps_revision_window"] = [first["as_of"], last["as_of"]]
    rex = _latest(conn, cid, "analyst_consensus", "revenue_ex_tac", key, t, kt, "mean")
    if rex:
        row["consensus_revenue_ex_tac"] = rex[0]["value"]
        row["consensus_revenue_ex_tac_evidence"] = rex[0]["eo_id"]
    rev = _latest(conn, cid, "analyst_consensus", "revenue", key, t, kt, "mean")
    if rev:
        row["consensus_revenue"], row["consensus_revenue_provider"] = rev[0]["value"], rev[0]["provider"]
        row["consensus_revenue_evidence"] = rev[0]["eo_id"]
    # outcomes
    eps_gaap = _obs(conn, ev["accession_no"], "eps_diluted") or _xbrl_first(conn, cid, key, "eps_diluted")
    rev_gaap = _obs(conn, ev["accession_no"], "revenue") or _xbrl_first(conn, cid, key, "revenue")
    street = conn.execute("""SELECT value FROM expectation_obs WHERE company_id=? AND class='reported_result' AND
                             metric='eps' AND stat='street_actual' AND target_period=? ORDER BY as_of LIMIT 1""",
                          (cid, key)).fetchone()
    row["eps_gaap"], row["revenue_gaap"] = eps_gaap, rev_gaap
    row["eps_street_actual"] = street["value"] if street else None
    if row.get("consensus_eps"):
        if row["eps_street_actual"] is not None:
            row["eps_surprise_street"] = row["eps_street_actual"] / row["consensus_eps"] - 1
        if eps_gaap is not None:
            row["eps_surprise_gaap"] = eps_gaap / row["consensus_eps"] - 1
            row["gaap_vs_street_gap"] = (None if row["eps_street_actual"] is None else
                                         eps_gaap - row["eps_street_actual"])
    if row.get("consensus_revenue") and rev_gaap:
        row["revenue_surprise"] = rev_gaap / row["consensus_revenue"] - 1
    # guidance in force
    g = {}
    for metric in ("revenue", "segment_revenue_intelligent_cloud", "segment_revenue_pbp", "segment_revenue_mpc",
                   "azure_growth_cc", "quantitative_guidance"):
        lo = _latest(conn, cid, "management_guidance", metric, key, t, kt, "low")
        hi = _latest(conn, cid, "management_guidance", metric, key, t, kt, "high")
        tx = _latest(conn, cid, "management_guidance", metric, key, t, kt, "text")
        if lo and hi:
            g[metric] = {"low": lo[0]["value"], "high": hi[0]["value"], "grade": lo[0]["provenance_grade"],
                         "as_of": lo[0]["as_of"], "evidence": [lo[0]["eo_id"], hi[0]["eo_id"]]}
        elif tx:
            g[metric] = {"text": tx[0]["value_text"], "grade": tx[0]["provenance_grade"], "evidence": [tx[0]["eo_id"]]}
    if "revenue" in g and "low" in g["revenue"] and rev_gaap:
        r_ = g["revenue"]
        r_["actual"] = rev_gaap
        r_["position"] = "above" if rev_gaap > r_["high"] else "below" if rev_gaap < r_["low"] else "within"
        r_["vs_midpoint"] = rev_gaap / ((r_["low"] + r_["high"]) / 2) - 1
    row["guidance"] = g
    # market-implied
    im = _latest(conn, cid, "market_implied", "implied_abs_move_event_day", key, t, kt, "point")
    if im and im[0]["value"] is not None:
        row["implied_move"] = im[0]["value"]
    pre = conn.execute("""SELECT value FROM expectation_obs WHERE company_id=? AND metric='implied_fcf_cagr_10y_erp_5pct'
                          AND target_period=? AND stat='pre' ORDER BY eo_id DESC LIMIT 1""", (cid, key)).fetchone()
    post = conn.execute("""SELECT value FROM expectation_obs WHERE company_id=? AND metric='implied_fcf_cagr_10y_erp_5pct'
                           AND target_period=? AND stat='post' ORDER BY eo_id DESC LIMIT 1""", (cid, key)).fetchone()
    if pre and pre["value"] is not None:
        row["implied_growth_pre"] = pre["value"]
    same = conn.execute("""SELECT value FROM expectation_obs WHERE company_id=? AND metric='implied_fcf_cagr_10y_erp_5pct'
                           AND target_period=? AND stat='post_same_fundamentals' ORDER BY eo_id DESC LIMIT 1""",
                        (cid, key)).fetchone()
    if pre and pre["value"] is not None:
        if same and same["value"] is not None:
            row["implied_growth_change"] = same["value"] - pre["value"]          # price-driven (fundamentals fixed)
        if post and post["value"] is not None and same and same["value"] is not None:
            row["implied_growth_fundamentals_effect"] = post["value"] - same["value"]   # new filing's cash flows
    row["narrative_items"] = conn.execute("""SELECT count(*) FROM expectation_obs WHERE company_id=? AND
                                             class='narrative' AND target_period=?""", (cid, key)).fetchone()[0]
    # reaction
    r = measure(conn, ev["event_id"], kt, expectations=False, store=store)
    if r.get("status") == "ok":
        w = r["windows"]
        row.update({"reaction_day": r["reaction_day"], "d0_return": w["d0"]["return"],
                    "car_d0": w["d0"].get("market_model", {}).get("car"),
                    "scar_d0": w["d0"].get("market_model", {}).get("scar"),
                    "gap": w["overnight_gap"]["return"], "attribution": r["attribution_uncertainty"]["level"],
                    "confounders_d0": sorted({(o["ticker"] or o["event_type"]) for o in r["overlapping_events"]
                                              if o["within"] == "d0_reaction_period" and o["kind"] != "same_company_companion_filing"})})
    return row


def build(conn, kt, tickers=("MSFT", "GOOGL", "META"), since="2016-01-01", store=None):
    rows = []
    for tk in tickers:
        cid = conn.execute("SELECT company_id FROM company WHERE ticker=?", (tk,)).fetchone()[0]
        for ev in conn.execute("""SELECT * FROM event WHERE company_id=? AND event_type='earnings_release' AND
                                  event_date>=? ORDER BY available_at""", (cid, since)).fetchall():
            rows.append(event_row(conn, ev, kt, store))
    return rows


def describe(rows):
    """Descriptive associations only, with n. No fitted model, no threshold search."""
    out = {}
    pairs = [(r["eps_surprise_street"], r["car_d0"]) for r in rows
             if r.get("eps_surprise_street") is not None and r.get("car_d0") is not None]
    if len(pairs) >= 8:
        x, y = zip(*pairs)
        agree = sum(1 for a, b in pairs if a * b > 0)
        out["street_eps_surprise_vs_car"] = {
            "n": len(pairs), "spearman": _spearman(x, y), "sign_agreement": agree / len(pairs),
            "binomial_p_two_sided": _binom_two_sided(agree, len(pairs)),
            "share_positive_surprise": sum(1 for a in x if a > 0) / len(x)}
    ims = [(r["implied_move"], abs(r["d0_return"])) for r in rows if r.get("implied_move") and r.get("d0_return") is not None]
    if ims:
        out["googl_implied_vs_realised_move"] = {
            "n": len(ims), "median_implied": statistics.median(a for a, _ in ims),
            "median_realised_abs": statistics.median(b for _, b in ims),
            "share_realised_above_implied": sum(1 for a, b in ims if b > a) / len(ims)}
    guid = [r["guidance"]["revenue"]["position"] for r in rows if r.get("guidance", {}).get("revenue", {}).get("position")]
    if guid:
        out["meta_revenue_vs_guidance"] = {p: guid.count(p) for p in ("below", "within", "above")}
    return out


def _rank(v):
    s = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    for k, i in enumerate(s):
        r[i] = k
    return r


def _spearman(x, y):
    rx, ry = _rank(list(x)), _rank(list(y))
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _binom_two_sided(k, n, p=0.5):
    from math import comb
    pk = comb(n, k) * p ** k * (1 - p) ** (n - k)
    return min(1.0, sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)
                        if comb(n, i) * p ** i * (1 - p) ** (n - i) <= pk + 1e-15))
