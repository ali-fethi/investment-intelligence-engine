"""Coverage report, expectation-linked event table, market-vs-fundamentals hypotheses, and the audit."""
from __future__ import annotations

import random
from collections import defaultdict

from ..db import companies


def pct(x, d=1):
    return "—" if x is None else f"{x * 100:+.{d}f}%"


# --------------------------------------------------------------------------------------------- coverage
def coverage(rows):
    def has(r, k):
        return r.get(k) is not None
    cov = defaultdict(lambda: defaultdict(int))
    for r in rows:
        c = cov[r["ticker"]]
        c["events"] += 1
        c["consensus_eps_pit"] += has(r, "consensus_eps")
        c["consensus_eps_fresh"] += has(r, "consensus_eps") and (r.get("consensus_eps_days_before") or 999) <= 30
        c["consensus_eps_revision_path"] += has(r, "consensus_eps_revision")
        c["consensus_revenue_pit"] += has(r, "consensus_revenue")
        c["consensus_revenue_ex_tac_pit"] += has(r, "consensus_revenue_ex_tac")
        c["street_actual"] += has(r, "eps_street_actual")
        g = r.get("guidance", {})
        c["guidance_numeric"] += any("low" in v for v in g.values())
        c["guidance_explicit_none"] += (g.get("quantitative_guidance", {}).get("text") or "").startswith(
            ("none in release", "no revenue/earnings"))
        c["implied_move"] += has(r, "implied_move")
        c["implied_growth"] += has(r, "implied_growth_pre")
        c["narrative"] += bool(r.get("narrative_items"))
        c["reaction"] += has(r, "car_d0")
    return cov


def render_coverage(rows) -> str:
    cov = coverage(rows)
    cols = [("consensus_eps_pit", "Consensus EPS (A/B, before release, ≤150 days old)"),
            ("consensus_eps_fresh", "…of which captured ≤30 days before release"),
            ("consensus_eps_revision_path", "…with revision path (≥2 dated captures)"),
            ("consensus_revenue_pit", "Consensus GAAP revenue (A/B)"),
            ("consensus_revenue_ex_tac_pit", "Consensus revenue ex-TAC (A/B; not comparable to GAAP)"),
            ("street_actual", "Street-basis actual (C, vendor)"),
            ("guidance_numeric", "Numeric management guidance (A/B)"),
            ("guidance_explicit_none", "Explicit 'no revenue/earnings guidance' (A)"),
            ("implied_move", "Options-implied move (B, model)"),
            ("implied_growth", "Price-implied FCF growth (B, model)"),
            ("narrative", "Narrative items (C)"),
            ("reaction", "Measured reaction")]
    L = ["| Expectation class | " + " | ".join(f"{t} (n={cov[t]['events']})" for t in sorted(cov)) + " | All |",
         "|---|" + "---:|" * (len(cov) + 1)]
    tot = sum(cov[t]["events"] for t in cov)
    for k, label in cols:
        s = sum(cov[t][k] for t in cov)
        L.append(f"| {label} | " + " | ".join(f"{cov[t][k]} ({cov[t][k] / cov[t]['events']:.0%})" for t in sorted(cov))
                 + f" | {s}/{tot} ({s / tot:.0%}) |")
    by_period = defaultdict(lambda: [0, 0])
    for r in rows:
        y = int(r["public_at"][:4])
        p = "2016-2019" if y < 2020 else "2020-2022" if y < 2023 else "2023-2026"
        by_period[p][0] += 1
        by_period[p][1] += r.get("consensus_eps") is not None
    L += ["", "Dated consensus EPS by period: " + "; ".join(
        f"{p}: {v[1]}/{v[0]}" for p, v in sorted(by_period.items()))]
    return "\n".join(L)


# --------------------------------------------------------------------------------------------- event table
def render_events(rows) -> str:
    L = ["| Ticker | Quarter | Public (UTC) | Cons. EPS (age d) | Revision | Street actual | Surprise (street) | "
         "GAAP EPS | Guidance | Implied move | Δ implied growth (price) | d0 | CAR | SCAR | Attribution | Confounders d0 |",
         "|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|---|"]
    for r in rows:
        g = r.get("guidance", {})
        gtxt = []
        if g.get("revenue", {}).get("position"):
            gtxt.append(f"rev {g['revenue']['position']}")
        if any(k.startswith("segment") for k in g):
            gtxt.append("segments")
        if g.get("azure_growth_cc"):
            gtxt.append("Azure")
        if g.get("quantitative_guidance", {}).get("text") == "none in release":
            gtxt.append("none")
        ce, age = r.get("consensus_eps"), r.get("consensus_eps_days_before")
        cons = "—" if ce is None else f"{ce:.2f} ({age}d)"
        st = "—" if r.get("eps_street_actual") is None else f"{r['eps_street_actual']:.2f}"
        ga = "—" if r.get("eps_gaap") is None else f"{r['eps_gaap']:.2f}"
        sc = "—" if r.get("scar_d0") is None else f"{r['scar_d0']:+.1f}"
        L.append(f"| {r['ticker']} | {r['quarter']} | {r['public_at'][:16]} | {cons} | "
                 f"{pct(r.get('consensus_eps_revision'))} | {st} | {pct(r.get('eps_surprise_street'))} | {ga} | "
                 f"{', '.join(gtxt) or '—'} | {pct(r.get('implied_move'))} | {pct(r.get('implied_growth_change'), 2)} | "
                 f"{pct(r.get('d0_return'))} | {pct(r.get('car_d0'))} | {sc} | {r.get('attribution', '—')} | "
                 f"{', '.join(r.get('confounders_d0', [])[:4])} |")
    return "\n".join(L)


# --------------------------------------------------------------------------------------------- hypotheses
def hypotheses(conn, cutoff, kt):
    """Reverse DCF v2: the revenue growth the price requires vs reported history. Numbers plus questions; no forecast,
    nothing fitted to the price. Not computed when an essential input is missing."""
    from . import valuation as V
    out = []
    for c in companies(conn):
        inp = V.inputs_at(conn, c, cutoff, kt)
        if inp["missing"]:
            out.append({"ticker": c["ticker"], "status": "not_computed", "missing": inp["missing"]})
            continue
        base, sens = V.implied_growth(inp), V.sensitivities(inp)
        g, tr = base["implied_revenue_cagr_y1_5"], inp["trailing_3y_revenue_cagr"]
        qs = []
        if g is not None and tr is not None:
            qs.append(f"The price requires ~{g:.1%}/yr revenue growth for five years (fading to {base['terminal_growth']:.1%}), "
                      f"at today's {base['margin']:.0%} operating margin and sales-to-capital {base['s2c']:.2f}; "
                      f"trailing 3-year revenue growth is {tr:.1%}.")
        qs.append(f"If capital intensity doubled (sales-to-capital x0.5) the requirement rises to "
                  f"{sens['other']['S2C x0.5 (capex intensity doubles)']:.1%}; if it halved, it falls to "
                  f"{sens['other']['S2C x2 (capex intensity halves)']:.1%}. The return on the current capex cycle is the "
                  "assumption the price is most sensitive to.")
        out.append({"ticker": c["ticker"], "status": "ok", "implied_revenue_cagr_y1_5": g, "trailing_3y_revenue_cagr": tr,
                    "wacc": base["wacc"], "margin": base["margin"], "s2c": base["s2c"], "flags": inp["flags"],
                    "sensitivities": sens, "questions": qs})
    return out


# --------------------------------------------------------------------------------------------- audit
def audit(conn, rows, store=None, sample=8, refetch=True, seed=7):
    res = {}
    # 1. leakage: every expectation used for an event must be available strictly before its public time
    viol = []
    for r in rows:
        for k in ("consensus_eps_evidence", "consensus_revenue_evidence", "consensus_revenue_ex_tac_evidence"):
            if r.get(k):
                a = conn.execute("SELECT available_at FROM expectation_obs WHERE eo_id=?", (r[k],)).fetchone()[0]
                if a is None or a >= r["public_at"]:
                    viol.append((r["quarter"], k, a))
        for m, g in r.get("guidance", {}).items():
            for e in g.get("evidence", []):
                a = conn.execute("SELECT available_at FROM expectation_obs WHERE eo_id=?", (e,)).fetchone()[0]
                if a is None or a >= r["public_at"]:
                    viol.append((r["quarter"], m, a))
    res["leakage_violations"] = viol
    # 2. grade C/D rows can never be point-in-time
    res["c_or_d_with_available_at"] = conn.execute("""SELECT count(*) FROM expectation_obs WHERE provenance_grade
                                                       IN ('C','D') AND available_at IS NOT NULL""").fetchone()[0]
    # 3. old-layout quarter-label validation
    lab = conn.execute("""SELECT json_extract(checks_json,'$.label_check') c, count(*) n FROM expectation_obs
                          WHERE method='marketwatch_parser_v1' AND stat='mean' AND
                          json_extract(checks_json,'$.label') LIKE 'inferred%' GROUP BY 1""").fetchall()
    res["inferred_label_checks"] = {r["c"]: r["n"] for r in lab}
    # 4. split rebasing applied
    res["split_rebased_observations"] = [dict(r) for r in conn.execute(
        """SELECT target_period, stat, value, adjustments FROM expectation_obs WHERE adjustments IS NOT NULL
           AND stat='mean'""")]
    # 5. guidance revision consistency: 'prior outlook of X-Y' must equal the previously published range
    chk = []
    for r in conn.execute("""SELECT * FROM expectation_obs WHERE provider='Meta management' AND notes LIKE
                             'revision: prior outlook%' AND stat='low'"""):
        prior = conn.execute("""SELECT value FROM expectation_obs WHERE provider='Meta management' AND metric=? AND
                                target_period=? AND stat='low' AND as_of<? ORDER BY as_of DESC LIMIT 1""",
                             (r["metric"], r["target_period"], r["as_of"])).fetchone()
        stated = float(r["notes"].split("outlook ")[1].split("-")[0]) * 1e9
        chk.append({"target": r["target_period"], "metric": r["metric"], "as_of": r["as_of"], "stated_prior_low": stated,
                    "previous_published_low": prior["value"] if prior else None,
                    "consistent": prior is not None and abs(prior["value"] - stated) < 1e6})
    res["guidance_revision_consistency"] = chk
    # 5b. guidance must target a period that has not ended when it is issued (catches mislabelled target years)
    bad_t = []
    for r in conn.execute("""SELECT eo_id, provider, metric, target_period, as_of, locator FROM expectation_obs
                             WHERE class='management_guidance' AND stat IN ('low','high','point')
                             AND target_period LIKE 'FY%'"""):
        # fiscal years are named by their end year, so a target year before the issue year has already ended
        if int(r["target_period"][2:6]) < int(r["as_of"][:4]):
            bad_t.append(dict(r))
    res["guidance_targets_already_ended"] = bad_t
    # 6. cross-provider: archived FactSet consensus vs dated Zacks previews, same quarter
    xp = []
    for r in conn.execute("""SELECT z.company_id, z.target_period, z.value zv, z.as_of za FROM expectation_obs z
                             WHERE z.class='analyst_consensus' AND z.metric='eps' AND z.stat='mean' AND
                             z.provider LIKE 'Zacks%' AND z.provenance_grade IN ('A','B')"""):
        f = conn.execute("""SELECT value, as_of, abs(julianday(as_of)-julianday(?)) d FROM expectation_obs
                            WHERE company_id=? AND target_period=? AND metric='eps' AND stat='mean'
                            AND provider LIKE 'FactSet%' AND provenance_grade='B' ORDER BY d LIMIT 1""",
                        (r["za"], r["company_id"], r["target_period"])).fetchone()
        if f:
            xp.append({"quarter": r["target_period"], "zacks": r["zv"], "factset": f["value"],
                       "rel_diff": r["zv"] / f["value"] - 1, "days_apart": round(f["d"], 1),
                       "comparable": f["d"] <= 14})
    res["cross_provider_eps"] = xp
    # 7. independent re-fetch of archived captures and re-parse (source revisions / store integrity)
    if refetch and store is not None and store.transport is not None:
        from .parsers import parse_marketwatch, parse_marketwatch_old
        caps = conn.execute("""SELECT DISTINCT source_doc_id, source_url, target_period, value FROM expectation_obs
                               WHERE method='marketwatch_parser_v1' AND stat='mean' AND provenance_grade='B'""").fetchall()
        caps = list(caps)
        random.Random(seed).shuffle(caps)
        out = []
        for c in caps[:sample]:
            ts, orig = c["source_url"].split("/web/")[1].split("/", 1)
            status, body, _ = store.transport.get(f"http://web.archive.org/web/{ts}id_/{orig}")
            p = parse_marketwatch(body) or {}
            q = f"{c['target_period'][-2:]} {c['target_period'][2:6]}"
            v = (p.get("forward", {}).get(q, {}) or {}).get("average")
            if v is None:
                o = parse_marketwatch_old(body)
                v = o["this_quarter"]["mean"] if o else None
            stored = conn.execute("SELECT sha256 FROM source_document WHERE doc_id=?", (c["source_doc_id"],)).fetchone()[0]
            import hashlib
            out.append({"capture": c["source_url"], "quarter": c["target_period"], "stored_value": c["value"],
                        "refetched_value": v, "bytes_identical": hashlib.sha256(body).hexdigest() == stored})
        res["archive_refetch"] = out
    return res
