"""Consensus / EPS comparability audit and statistical-quality report (Sprint 2.1).

Every release is classified on four axes, then statistics are recomputed on explicitly defined samples.
  consensus:      none | age bucket of the latest usable (A/B) consensus EPS
  actual basis:   street actual known (vendor, C) and/or GAAP actual (A)
  comparability:  S  street vs street (consensus and actual on the same 'street' basis)
                  G= GAAP vs consensus where the street actual equals GAAP (basis verified)
                  G? GAAP vs consensus, street actual unknown (basis unverified)
                  X  GAAP differs from street actual: GAAP surprise not comparable (street used if available)
                  -  no consensus
  issues:         stale (>30 d), cross-provider disagreement, gross outlier, label mismatch, mixed provider
Statistics: Spearman/Pearson, sign agreement, permutation p-values (seeded), bootstrap and date-clustered
bootstrap 95% intervals, sensitivity to sample selection. Nothing is fitted and nothing is a trading signal.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict

AGE_BUCKETS = [(0, 7, "0-7d"), (8, 30, "8-30d"), (31, 60, "31-60d"), (61, 150, "61-150d")]
SEED = 20261107


def _age_bucket(d):
    if d is None:
        return None
    for lo, hi, name in AGE_BUCKETS:
        if lo <= d <= hi:
            return name
    return ">150d"


def street_actuals(conn):
    """Street-basis actual EPS by (company_id, quarter), from two vendor records (both grade C):
       1. 'Actual' in the history block of a page captured after the release (2020+ layout)
       2. 'Last Quarter's Earnings' on an old-layout page captured after the release and before the next one."""
    out = {}
    for r in conn.execute("""SELECT company_id, target_period, value FROM expectation_obs WHERE class='reported_result'
                             AND stat='street_actual' ORDER BY as_of"""):
        out.setdefault((r["company_id"], r["target_period"]), (r["value"], "history block"))
    for r in conn.execute("""SELECT company_id, target_period, checks_json FROM expectation_obs
                             WHERE method='marketwatch_parser_v1' AND stat='mean'
                             AND json_extract(checks_json,'$.label') LIKE 'inferred%'"""):
        lq = json.loads(r["checks_json"]).get("last_quarter_eps_page")
        if lq is None:
            continue
        tp = r["target_period"]
        fy, q = int(tp[2:6]), int(tp[-1])
        prev = f"FY{fy - 1}Q4" if q == 1 else f"FY{fy}Q{q - 1}"
        out.setdefault((r["company_id"], prev), (lq, "old-layout 'Last Quarter's Earnings'"))
    return out


def classify(conn, rows):
    cid = {r["ticker"]: r["company_id"] for r in conn.execute("SELECT ticker, company_id FROM company")}
    sa = street_actuals(conn)
    xprov = defaultdict(list)
    for r in conn.execute("""SELECT company_id, target_period, provider, value, as_of FROM expectation_obs
                             WHERE class='analyst_consensus' AND metric='eps' AND stat='mean'
                             AND provenance_grade IN ('A','B')"""):
        xprov[(r["company_id"], r["target_period"])].append(r)
    out = []
    for r in rows:
        c = cid[r["ticker"]]
        k = (c, r["quarter"])
        rec = {k2: r.get(k2) for k2 in ("ticker", "quarter", "public_at", "reaction_day", "consensus_eps",
                                        "consensus_eps_provider", "consensus_eps_days_before", "eps_gaap", "car_d0",
                                        "scar_d0", "attribution")}
        rec["age_bucket"] = _age_bucket(r.get("consensus_eps_days_before"))
        st = sa.get(k)
        rec["street_actual"], rec["street_actual_source"] = (st if st else (None, None))
        issues = []
        cons, gaap, street = r.get("consensus_eps"), r.get("eps_gaap"), rec["street_actual"]
        if cons is None:
            rec["comparability"] = "-"
        elif street is not None:
            rec["comparability"] = "S"
            rec["surprise"] = street / cons - 1 if cons else None
            if gaap is not None:
                rec["gaap_minus_street"] = gaap - street
                rec["gaap_basis_equal"] = abs(gaap - street) <= 0.011
            if not str(r.get("consensus_eps_provider", "")).startswith("FactSet"):
                issues.append("mixed provider: consensus not FactSet, street actual FactSet")
        elif gaap is not None:
            rec["comparability"] = "G?"
            rec["surprise"] = gaap / cons - 1 if cons else None
        else:
            rec["comparability"] = "-"
        if rec["comparability"] == "S" and rec.get("gaap_basis_equal") is False:
            rec["comparability_gaap"] = "X"
        elif rec["comparability"] == "S" and rec.get("gaap_basis_equal"):
            rec["comparability_gaap"] = "G="
        if cons is not None and r.get("consensus_eps_days_before") is not None and r["consensus_eps_days_before"] > 30:
            issues.append("stale consensus (>30 days before release)")
        if cons is not None:
            # provider disagreement among captures within 14 days of the chosen one
            near = [o for o in xprov.get(k, []) if o["as_of"] < r["public_at"]]
            vals = [o["value"] for o in near]
            if len(vals) >= 2 and max(vals) / min(vals) - 1 > 0.03 and min(vals) > 0:
                spread = max(vals) / min(vals) - 1
                issues.append(f"consensus captures disagree by {spread:.1%} (all pre-release captures, any age)")
            ref = street if street is not None else gaap
            if ref is not None and cons > 0 and not (0.5 <= ref / cons <= 2.0):
                issues.append("gross outlier: actual/consensus outside [0.5, 2]")
        rec["issues"] = issues
        out.append(rec)
    return out


# --------------------------------------------------------------------------------------------- statistics
def _rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def pearson(x, y):
    mx, my = statistics.mean(x), statistics.mean(y)
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx and sy else None


def spearman(x, y):
    return pearson(_rank(list(x)), _rank(list(y)))


def stats_for(pairs, clusters=None, n_perm=5000, n_boot=2000):
    if len(pairs) < 8:
        return {"n": len(pairs), "note": "too few observations"}
    x, y = [p[0] for p in pairs], [p[1] for p in pairs]
    rho, r = spearman(x, y), pearson(x, y)
    agree = sum(1 for a, b in pairs if a * b > 0) / len(pairs)
    rnd = random.Random(SEED)
    ge = 0
    for _ in range(n_perm):
        yy = y[:]
        rnd.shuffle(yy)
        if abs(spearman(x, yy)) >= abs(rho) - 1e-12:
            ge += 1
    boot = []
    idx = list(range(len(pairs)))
    for _ in range(n_boot):
        s = [rnd.choice(idx) for _ in idx]
        v = spearman([x[i] for i in s], [y[i] for i in s])
        if v is not None:
            boot.append(v)
    boot.sort()
    res = {"n": len(pairs), "spearman": rho, "pearson": r, "sign_agreement": agree,
           "perm_p_two_sided": (ge + 1) / (n_perm + 1),
           "boot_ci95": [boot[int(0.025 * len(boot))], boot[int(0.975 * len(boot)) - 1]]}
    if clusters:
        groups = defaultdict(list)
        for i, c in enumerate(clusters):
            groups[c].append(i)
        keys = list(groups)
        cb = []
        for _ in range(n_boot):
            s = [i for k in (rnd.choice(keys) for _ in keys) for i in groups[k]]
            v = spearman([x[i] for i in s], [y[i] for i in s])
            if v is not None:
                cb.append(v)
        cb.sort()
        res["cluster_boot_ci95"] = [cb[int(0.025 * len(cb))], cb[int(0.975 * len(cb)) - 1]]
        res["n_clusters"] = len(keys)
    return res


def _week(d):
    from datetime import date
    y, w, _ = date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def _age(c):
    """Days between consensus capture and release; 0 is a valid age (do not use `or` defaults)."""
    d = c.get("consensus_eps_days_before")
    return 10 ** 6 if d is None else d


def report(conn, rows):
    cls = classify(conn, rows)
    usable = [c for c in cls if c.get("surprise") is not None and c.get("car_d0") is not None]

    def sample(pred):
        s = [c for c in usable if pred(c)]
        return stats_for([(c["surprise"], c["car_d0"]) for c in s], clusters=[_week(c["reaction_day"]) for c in s])

    def winsor(vals, p=0.05):
        v = sorted(vals)
        lo, hi = v[int(p * len(v))], v[int((1 - p) * len(v)) - 1]
        return [min(max(a, lo), hi) for a in vals]
    street = [c for c in usable if c["comparability"] == "S"]
    w_x = winsor([c["surprise"] for c in street])
    w_y = winsor([c["car_d0"] for c in street])
    cars = sorted(abs(c["car_d0"]) for c in street)
    cut = cars[int(0.95 * len(cars)) - 1] if cars else None
    samples = {
        "S: street vs street (primary)": sample(lambda c: c["comparability"] == "S"),
        "S, consensus ≤7 days old": sample(lambda c: c["comparability"] == "S" and _age(c) <= 7),
        "S, consensus ≤30 days old": sample(lambda c: c["comparability"] == "S" and _age(c) <= 30),
        "S, FactSet consensus only (same provider as actual)": sample(
            lambda c: c["comparability"] == "S" and str(c["consensus_eps_provider"]).startswith("FactSet")),
        "S, excluding high attribution uncertainty (macro on d0)": sample(
            lambda c: c["comparability"] == "S" and c["attribution"] != "high"),
        "S, excluding top 5% |CAR|": sample(lambda c: c["comparability"] == "S" and abs(c["car_d0"]) < (cut or 9)),
        "S, winsorised 5/95 (surprise and CAR)": stats_for(list(zip(w_x, w_y))),
        "G?: GAAP vs consensus, basis unverified": sample(lambda c: c["comparability"] == "G?"),
        "S ∪ G?: all releases with consensus and any actual": sample(lambda c: True),
    }
    for tk in ("GOOGL", "META", "MSFT"):
        samples[f"S, {tk} only"] = sample(lambda c, tk=tk: c["comparability"] == "S" and c["ticker"] == tk)
    reconciliation = {
        "releases": len(cls),
        "with dated consensus EPS (A/B, ≤150 d)": sum(1 for c in cls if c["consensus_eps"] is not None),
        "by age bucket": {b: sum(1 for c in cls if c["age_bucket"] == b) for _, _, b in AGE_BUCKETS},
        "with street actual (vendor)": sum(1 for c in cls if c["consensus_eps"] is not None and c["street_actual"] is not None),
        "street actual source": {s: sum(1 for c in cls if c["consensus_eps"] is not None and c["street_actual_source"] == s)
                                 for s in ("history block", "old-layout 'Last Quarter's Earnings'")},
        "comparability S (street vs street)": sum(1 for c in cls if c["comparability"] == "S"),
        "…of which GAAP = street (G=)": sum(1 for c in cls if c.get("comparability_gaap") == "G="),
        "…of which GAAP ≠ street (X)": sum(1 for c in cls if c.get("comparability_gaap") == "X"),
        "comparability G? (GAAP only, basis unverified)": sum(1 for c in cls if c["comparability"] == "G?"),
        "no consensus": sum(1 for c in cls if c["comparability"] == "-" and c["consensus_eps"] is None),
        "consensus but no actual": sum(1 for c in cls if c["consensus_eps"] is not None and c.get("surprise") is None),
        "with measured reaction among S": sum(1 for c in usable if c["comparability"] == "S"),
    }
    issues = defaultdict(int)
    for c in cls:
        for i in c["issues"]:
            issues[i.split(" (")[0].split(" by ")[0]] += 1
    gaps = [c for c in cls if c.get("comparability_gaap") == "X"]
    return {"reconciliation": reconciliation, "issues": dict(issues), "samples": samples,
            "gaap_vs_street_differences": [{k: c[k] for k in ("ticker", "quarter", "eps_gaap", "street_actual",
                                                              "gaap_minus_street")} for c in gaps],
            "n_statistics_reported": len(samples), "releases": cls}


def render(rep) -> str:
    r = rep["reconciliation"]
    L = ["# Consensus / EPS comparability and statistical quality", "",
         "## Reconciliation: from 133 releases to the comparable sample", "", "| Step | Releases |", "|---|---:|"]
    for k, v in r.items():
        L.append(f"| {k} | {v if not isinstance(v, dict) else ', '.join(f'{a}: {b}' for a, b in v.items())} |")
    L += ["", "## Data issues", "", "| Issue | Releases |", "|---|---:|"] + \
        [f"| {k} | {v} |" for k, v in sorted(rep["issues"].items())]
    L += ["", "## GAAP vs street EPS (where the vendor's street actual is known and differs)", "",
          "| Ticker | Quarter | GAAP | Street | GAAP − street |", "|---|---|---:|---:|---:|"]
    for g in rep["gaap_vs_street_differences"]:
        L.append(f"| {g['ticker']} | {g['quarter']} | {g['eps_gaap']:.2f} | {g['street_actual']:.2f} | "
                 f"{g['gaap_minus_street']:+.2f} |")
    L += ["", "## Surprise vs market-model abnormal return on d0", "",
          f"{rep['n_statistics_reported']} samples are reported; with this many looks, a nominal p < 0.05 is expected "
          "by chance in about one sample even if there were no relation.", "",
          "| Sample | n | Spearman | 95% CI (bootstrap) | 95% CI (clustered by reaction week) | Pearson | Sign agreement | "
          "Permutation p |", "|---|---:|---:|---|---|---:|---:|---:|"]
    for name, s in rep["samples"].items():
        if "spearman" not in s:
            L.append(f"| {name} | {s['n']} | — | — | — | — | — | — |")
            continue
        cci = s.get("cluster_boot_ci95")
        cl = "—" if not cci else f"[{cci[0]:+.2f}, {cci[1]:+.2f}] ({s.get('n_clusters')} weeks)"
        ci = f"[{s['boot_ci95'][0]:+.2f}, {s['boot_ci95'][1]:+.2f}]"
        L.append(f"| {name} | {s['n']} | {s['spearman']:+.2f} | {ci} | {cl} | {s['pearson']:+.2f} | "
                 f"{s['sign_agreement']:.0%} | {s['perm_p_two_sided']:.3f} |")
    return "\n".join(L) + "\n"
