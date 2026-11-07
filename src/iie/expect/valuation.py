"""Reverse DCF v2 (Sprint 2.1): what the market price implies, with every input sourced and point-in-time.

Model (enterprise value, FCFF, after Damodaran's "sales-to-capital" formulation):
    Revenue   R_t = R_{t-1} (1 + g_t);  g_t = g for t = 1..5, then linear fade to g_T at t = 10
    NOPAT_t   = R_t * m * (1 - tau)                      m = TTM operating margin (GAAP EBIT: SBC already expensed)
    Reinvest_t = (R_t - R_{t-1}) / S2C                    S2C = 3y delta revenue / 3y (capex - depreciation)
    FCFF_t    = NOPAT_t - Reinvest_t
    ROIC      incremental ROIC in years 1-5 = m (1-tau) S2C; in years 6-10 it fades linearly to RONIC
              (S2C_t = ROIC_t / (m (1-tau))), so returns on new capital converge as growth converges (v2.1)
    Terminal  FCFF_11 = R_10 (1+g_T) m (1-tau) (1 - g_T / RONIC);  TV = FCFF_11 / (WACC - g_T)
    EV        = sum FCFF_t / (1+WACC)^t + TV / (1+WACC)^10
    Equity    = EV + cash & short-term investments - debt - finance leases     (non-marketable investments excluded)
    Solve g such that Equity = market value of equity (as-traded price x shares).
Discount rate (CAPM / WACC), all point-in-time at the valuation date:
    r_f   10-year Treasury par yield (U.S. Treasury)
    ERP   Damodaran implied ERP, monthly 'ERP (T12m)' (same trailing-12-month cash-yield definition as the annual
          'Implied ERP (FCFE)'), latest month whose publication is evidenced before the cutoff (v2.1)
    beta  OLS on 104 weekly total returns vs SPY, Blume-adjusted (0.67 b + 0.33), from stored prices
    r_d   Moody's Aaa corporate yield (FRED AAA, monthly)      tax  TTM effective rate, bounded to [10%, 30%]
    WACC  = E/(D+E) (r_f + beta ERP) + D/(D+E) r_d (1 - tax),  D = book debt + finance leases
Terminal growth g_T = min(r_f, 10-year breakeven inflation + 1.0% real), RONIC = WACC (no value created by
growth in perpetuity) in the base case, reached by the year 6-10 fade. When an essential input is missing, the result is an explicit
`not_computed` with the reason, never a number.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from ..config import ALL_METRICS
from ..derive import FiscalIndex, as_traded_close, latest_quarter, split_factor_between, ttm
from ..ingest.macro import _insert_if_new, _read_fred_csv
from ..pit import corporate_actions, latest_bar, load_facts, macro_value
from ..timeutil import et_to_utc_str, parse_utc

DAMODARAN_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls"
ERP_SERIES = "DAMODARAN:ERP_FCFE"      # parsed from the spreadsheet column 'Implied ERP (FCFE)'
# superseded: 'DAMODARAN:ERP_IMPLIED_FCFE' was parsed from the HTML page and took the DDM column on long rows
ERP_MONTHLY_URL = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
ERP_MONTHLY_SERIES = "DAMODARAN:ERP_T12M_MONTHLY"   # column 'ERP (T12m)', value at the start of each month
ERP_ASSUMED_LAG_DAYS = 14       # only when no dated capture exists: assumed publication 14 days after month start
AAA_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=AAA&cosd=2010-01-01"
REAL_GROWTH = 0.01
TAX_BOUNDS = (0.10, 0.30)
YEARS, HIGH = 10, 5


# ----------------------------------------------------------------------------------------- sourced inputs
def ingest_sources(conn, store, run_id, stats):
    """Damodaran annual implied ERP and Moody's Aaa yield into macro_observation (bitemporal, append-only)."""
    f = store.fetch(DAMODARAN_URL, source_system="damodaran", doc_type="erp_table_xls", run_id=run_id,
                    title="Damodaran histimpl.xls (historical implied ERP, US)")
    n = 0
    for y, prem in parse_damodaran_xls(f.body):
        # computed at year-end y, published in the first days of January y+1 (conservative: 10 January)
        n += _insert_if_new(conn, dict(
            series_id=ERP_SERIES, obs_date=f"{y}-12-31", value=prem, unit="ratio",
            definition="Implied equity risk premium on the S&P 500, FCFE method, at year-end (A. Damodaran, histimpl.xls)",
            vintage_date=None, available_at=et_to_utc_str(date(y + 1, 1, 10), 0, 0),
            availability_basis="assumed publication by 10 January of the following year; historical values taken from "
                               "the current file (past revisions by the author, if any, are not observable)",
            availability_verified=0, source_doc_id=f.doc_id, source_url=f.url, retrieved_at=f.retrieved_at,
            natural_key=f"damodaran|erp_fcfe|{y}", run_id=run_id))
    g = store.fetch(AAA_URL, source_system="fred", doc_type="fred_csv", run_id=run_id, title="FRED AAA (Moody's Aaa)")
    for obs_date, val in _read_fred_csv(g.body):
        d = date.fromisoformat(obs_date)
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        n += _insert_if_new(conn, dict(
            series_id="FRED:AAA", obs_date=obs_date, value=val, unit="Percent",
            definition="Moody's seasoned Aaa corporate bond yield, monthly average", vintage_date=None,
            available_at=et_to_utc_str(nxt + timedelta(days=7), 17, 0),
            availability_basis="monthly average; assumed available 7 days after month end", availability_verified=0,
            source_doc_id=g.doc_id, source_url=g.url, retrieved_at=g.retrieved_at,
            natural_key=f"fred|AAA|{obs_date}", run_id=run_id))
    n += ingest_erp_monthly(conn, store, run_id, stats)
    stats["valuation_source_obs_new"] = n
    return n


def parse_damodaran_xls(body: bytes):
    import io
    import pandas as pd
    df = pd.read_excel(io.BytesIO(body), sheet_name=0, header=None)
    h = next(i for i in range(len(df)) if str(df.iloc[i, 0]).strip() == "Year")
    cols = [str(c).strip() for c in df.iloc[h]]
    j = cols.index("Implied ERP (FCFE)")
    out = []
    for i in range(h + 1, len(df)):
        y, v = df.iloc[i, 0], df.iloc[i, j]
        try:
            y, v = int(float(y)), float(v)
        except (TypeError, ValueError):
            continue
        if 1950 < y < 2100 and v == v:
            out.append((y, v))
    return out


def _pct(v):
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
        return float(v[:-1]) / 100 if v.endswith("%") else float(v)
    return float(v)


def parse_damodaran_monthly(body: bytes):
    """[(YYYY-MM-01, ERP (T12m))] from ERPbymonth.xlsx (or an archived copy). Returns [] if the layout differs."""
    import io
    import warnings
    import pandas as pd
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            book = pd.read_excel(io.BytesIO(body), sheet_name=None, header=None)
        except Exception:
            return []
    df = book.get("Historical ERP")
    if df is None:
        return []
    hs = [i for i in range(min(len(df), 10)) if str(df.iloc[i, 0]).strip() == "Start of month"]
    if not hs:
        return []
    cols = [str(c).strip() for c in df.iloc[hs[0]]]
    if "ERP (T12m)" not in cols:
        return []
    j = cols.index("ERP (T12m)")
    out = []
    for i in range(hs[0] + 1, len(df)):
        d, v = df.iloc[i, 0], df.iloc[i, j]
        try:
            d = pd.Timestamp(d).date()
            v = _pct(v)
        except (TypeError, ValueError):
            continue
        if v == v and 0 < v < 0.2:
            out.append((d.replace(day=1).isoformat(), round(v, 6)))
    return out


def ingest_erp_monthly(conn, store, run_id, stats):
    """Monthly implied ERP with vintages. Every Wayback capture of the file is a dated vintage: a value is available
    from the first capture that shows it (verified). The current file is a vintage at retrieval. A month whose first
    evidence comes more than 45 days after the month began is given an assumed availability (month start + 14 days,
    unverified): the file existed, but its publication date for that month cannot be observed."""
    from . import archive
    vint = []                                         # (available_at, value-by-month, doc_id, url, retrieved_at, basis)
    for cap in archive.list_captures(store, ERP_MONTHLY_URL.split("://")[1], start="2016", end="2026", run_id=run_id):
        doc_id, body = archive.fetch_capture(store, cap, run_id=run_id, title="Damodaran ERPbymonth.xlsx (archived)")
        rows = parse_damodaran_monthly(body) if body else []
        if rows:
            r = conn.execute("SELECT url, retrieved_at FROM source_document WHERE doc_id=?", (doc_id,)).fetchone()
            vint.append((cap["served_at"], dict(rows), doc_id, r["url"], r["retrieved_at"], "wayback_capture"))
    f = store.fetch(ERP_MONTHLY_URL, source_system="damodaran", doc_type="erp_table_xlsx", run_id=run_id,
                    title="Damodaran ERPbymonth.xlsx (monthly implied ERP, US)")
    cur = parse_damodaran_monthly(f.body)
    vint.append((f.retrieved_at, dict(cur), f.doc_id, f.url, f.retrieved_at, "retrieval"))
    vint.sort(key=lambda v: v[0])
    n, revisions, seen = 0, [], {}
    for at, vals, doc_id, url, ret, basis in vint:
        for m, v in sorted(vals.items()):
            if seen.get(m) == v:
                continue
            first = m not in seen
            if not first:
                revisions.append({"month": m, "from": seen[m], "to": v, "seen_at": at})
            seen[m] = v
            assumed = first and at > et_to_utc_str(date.fromisoformat(m) + timedelta(days=45), 0, 0)
            avail = et_to_utc_str(date.fromisoformat(m) + timedelta(days=ERP_ASSUMED_LAG_DAYS), 0, 0) if assumed else at
            nk = f"damodaran|erp_t12m|{m}|{v}"
            if conn.execute("SELECT 1 FROM macro_observation WHERE natural_key=?", (nk,)).fetchone():
                continue                                # append-only: re-runs add nothing already recorded
            cur_ = conn.execute(
                """INSERT OR IGNORE INTO macro_observation(series_id, obs_date, value, unit, definition, vintage_date,
                     available_at, availability_basis, availability_verified, source_doc_id, source_url, retrieved_at,
                     natural_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ERP_MONTHLY_SERIES, m, v, "ratio",
                 "Implied ERP on the S&P 500 at the start of the month, trailing-12-month cash yield "
                 "(A. Damodaran, ERPbymonth.xlsx, column 'ERP (T12m)')", at[:10], avail,
                 f"assumed: month start + {ERP_ASSUMED_LAG_DAYS} days (first dated evidence {at[:10]})" if assumed else
                 ("first Wayback capture showing this value" if basis == "wayback_capture" else
                  "first seen at retrieval of the current file"),
                 0 if assumed else 1, doc_id, url, ret, nk, run_id))
            n += cur_.rowcount
    stats["erp_monthly_vintages"] = len(vint)
    stats["erp_monthly_revisions"] = revisions
    return n


def erp_definition_check(conn, kt):
    """Annual 'Implied ERP (FCFE)' at year-end Y vs monthly 'ERP (T12m)' at 1 January Y+1 (same date, same method)."""
    out = []
    for r in conn.execute("SELECT obs_date, value FROM macro_observation WHERE series_id=? AND retrieved_at<=? "
                          "ORDER BY obs_date", (ERP_SERIES, kt)):
        y = int(r["obs_date"][:4])
        m = conn.execute("SELECT value FROM macro_observation WHERE series_id=? AND obs_date=? AND retrieved_at<=? "
                         "ORDER BY vintage_date DESC LIMIT 1", (ERP_MONTHLY_SERIES, f"{y + 1}-01-01", kt)).fetchone()
        if m:
            out.append({"year_end": y, "annual_fcfe": r["value"], "monthly_t12m_jan": m["value"],
                        "difference": m["value"] - r["value"]})
    return out


def _macro_at(conn, series, cutoff, kt):
    return macro_value(conn, series, cutoff, kt)


def beta_at(conn, company_id, cutoff, kt, weeks=104):
    """OLS beta of weekly total returns vs SPY over the `weeks` weeks ending at the last close before cutoff."""
    from ..reaction import PriceSeries
    spy_id = conn.execute("SELECT company_id FROM company WHERE ticker='SPY'").fetchone()[0]
    s, m = PriceSeries(conn, company_id, kt), PriceSeries(conn, spy_id, kt)
    last = et_to_utc_str(parse_utc(cutoff).date(), 16, 0)
    days = [d for d in m.dates if et_to_utc_str(date.fromisoformat(d), 16, 0) <= cutoff]
    if not days:
        return None
    # weekly sampling: last trading day of each ISO week
    weekly = {}
    for d in days:
        y, w, _ = date.fromisoformat(d).isocalendar()
        weekly[(y, w)] = d
    pts = sorted(weekly.values())[-(weeks + 1):]
    xs, ys = [], []
    for a, b in zip(pts, pts[1:]):
        rs, rm = _span_return(s, m.dates, a, b), _span_return(m, m.dates, a, b)
        if rs is not None and rm is not None:
            ys.append(rs)
            xs.append(rm)
    if len(xs) < 52:
        return None
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    resid = [y - my - b * (x - mx) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(e * e for e in resid) / (n - 2) / sxx)
    return {"raw": b, "blume": 0.67 * b + 0.33, "se": se, "n_weeks": n, "first": pts[0], "last": pts[-1],
            "unused_last": last}


def _span_return(series, cal, a, b):
    i, j = cal.index(a), cal.index(b)
    f = 1.0
    for k in range(i + 1, j + 1):
        seg = series.segment_factor(cal[k - 1], cal[k], "close")
        if seg is None:
            return None
        f *= seg
    return f - 1


def _instant(idx, metric, on_or_before=None):
    vals = idx.instants.get(metric, {})
    if not vals:
        return None, None
    d = max(k for k in vals if on_or_before is None or k <= on_or_before) if any(
        on_or_before is None or k <= on_or_before for k in vals) else None
    return (vals[d], d) if d else (None, None)


def inputs_at(conn, company, cutoff, kt, fundamentals_cutoff=None):
    """Every model input with its source row and availability. Missing essentials are listed in 'missing'."""
    cid = company["company_id"]
    idx = FiscalIndex(load_facts(conn, cid, fundamentals_cutoff or cutoff, kt, metrics=ALL_METRICS))
    lq = latest_quarter(idx, "revenue")
    out = {"missing": [], "flags": [], "sources": {}}
    if lq is None:
        out["missing"].append("revenue")
        return out
    fy, q = lq
    out["as_of_quarter"] = f"FY{fy}Q{q}"

    def t(metric, essential=True, fyq=(fy, q)):
        it = ttm(idx, metric, *fyq)
        if it is None:
            if essential:
                out["missing"].append(f"TTM {metric}")
            return None
        # one lineage entry per (metric, period): the 3-years-ago revenue TTM used to overwrite the current one
        key = f"ttm_{metric}" if fyq == (fy, q) else f"ttm_{metric}_FY{fyq[0]}Q{fyq[1]}"
        out["sources"][key] = {"period": f"TTM to FY{fyq[0]}Q{fyq[1]}", "inputs": [i["id"] for i in it["inputs"]],
                               "fiscal_periods": [i.get("fiscal_period") or i.get("role") for i in it["inputs"]],
                               "available_at": it["available_at"]}
        return it["value"]
    rev, ebit = t("revenue"), t("operating_income")
    # used only by the v1 -> v2 bridge, not by the v2 model:
    cfo, capex = t("operating_cash_flow", False), t("capex", False)
    dep, sbc = t("depreciation", False), t("sbc", False)
    pre, tax = t("pretax_income", False), t("income_tax", False)
    tax_basis = "TTM"
    if pre is None or tax is None:
        fys_ = sorted({k[1] for k in idx.by_label if k[0] == "pretax_income" and k[2] == "FY"})
        if fys_ and idx.get("income_tax", fys_[-1], "FY"):
            pre, tax = idx.get("pretax_income", fys_[-1], "FY").value, idx.get("income_tax", fys_[-1], "FY").value
            tax_basis = f"FY{fys_[-1]} (TTM not available)"
        else:
            out["flags"].append("no income-tax data: 21% statutory rate used")
    interest = t("interest_expense", essential=False)
    rev3 = t("revenue", True, (fy - 3, q))
    # sales-to-capital from the last three COMPLETE fiscal years (annual figures exist for every company; quarterly
    # depreciation does not, e.g. Microsoft before FY2025): delta revenue FY(L-3)->FY(L) / sum of (capex - depreciation)
    net_capex_3y, rev_add_3y, s2c_years, s2c_missing = None, None, None, []
    fys = sorted({k[1] for k in idx.by_label if k[0] == "revenue" and k[2] == "FY"})
    if fys:
        L = fys[-1]
        need = [idx.get("revenue", L, "FY"), idx.get("revenue", L - 3, "FY")] + \
            [idx.get(m, L - k, "FY") for m in ("capex", "depreciation") for k in range(3)]
        if all(need):
            rev_add_3y = need[0].value - need[1].value
            net_capex_3y = sum(idx.get("capex", L - k, "FY").value - idx.get("depreciation", L - k, "FY").value
                               for k in range(3))
            s2c_years = f"FY{L - 2}-FY{L}"
            out["sources"]["sales_to_capital"] = {"inputs": [f.obs_id for f in need],
                                                  "available_at": max(f.available_at for f in need)}
        else:
            names = ["revenue FY%d" % L, "revenue FY%d" % (L - 3)] + [f"{m} FY{L - k}" for m in ("capex", "depreciation")
                                                                     for k in range(3)]
            s2c_missing = [nm for nm, v in zip(names, need) if v is None]
    # balance sheet at the latest balance-sheet date
    ta = idx.instants.get("total_assets", {})
    bs_date = max(ta) if ta else None
    out["balance_sheet_date"] = bs_date

    def inst(metric, essential=False):
        f = idx.instants.get(metric, {}).get(bs_date) if bs_date else None
        if f is None:
            if essential:
                out["missing"].append(metric)
            return None
        out["sources"][metric] = {"inputs": [f.obs_id], "available_at": f.available_at}
        return f.value
    cash_st = inst("cash_st_investments")
    if cash_st is None:
        c, ms = inst("cash_and_equivalents"), inst("marketable_securities_current")
        cash_st = (c or 0) + (ms or 0) if c is not None else None
        if ms is None:
            out["flags"].append("marketable securities not tagged at balance-sheet date: cash only")
    if cash_st is None:
        out["missing"].append("cash")
    d_cur, d_non, cp, fl = inst("debt_current"), inst("debt_noncurrent"), inst("commercial_paper"), \
        inst("finance_lease_liability")
    if d_non is None and d_cur is None:
        out["flags"].append("no long-term debt tagged at balance-sheet date: debt = 0 assumed")
    for name, v in (("debt_current", d_cur), ("commercial_paper", cp), ("finance_lease_liability", fl)):
        if v is None:
            out["flags"].append(f"{name} not tagged at {bs_date}: treated as 0")
    debt = (d_cur or 0) + (d_non or 0) + (cp or 0)
    leases = fl or 0
    # shares: cover page (all classes, undimensioned) preferred; else diluted weighted average; else NI / EPS
    sh, sh_basis, sh_avail = None, None, None
    cover = idx.instants.get("shares_outstanding_cover", {})
    if cover:
        dmax = max(cover)
        sh, sh_basis, sh_avail = cover[dmax].value, f"cover page shares outstanding as of {dmax}", cover[dmax].available_at
    else:
        for cand in ((fy, f"Q{q}"), (fy, "FY") if q == 4 else None, (fy, f"Q{q - 1}") if q > 1 else (fy - 1, "FY")):
            if cand and sh is None:
                f = idx.get("diluted_shares", cand[0], cand[1])
                if f is not None:
                    sh, sh_basis, sh_avail = f.value, f"diluted weighted-average shares FY{cand[0]}{cand[1]}", f.available_at
                else:
                    ni, eps = idx.get("net_income", cand[0], cand[1]), idx.get("eps_diluted", cand[0], cand[1])
                    if ni is not None and eps is not None and eps.value:
                        sh = ni.value / eps.value
                        sh_basis = f"net income / diluted EPS FY{cand[0]}{cand[1]} (derived)"
                        sh_avail = max(ni.available_at, eps.available_at)
        if sh is not None:
            out["flags"].append(f"share count approximated by {sh_basis}: the cover-page count is reported per class "
                                "(dimensional XBRL, absent from companyfacts)")
    if sh is None:
        out["missing"].append("share count")
    bar = latest_bar(conn, cid, cutoff, kt)
    rf = _macro_at(conn, "UST:10 Yr", cutoff, kt)
    erp = _macro_at(conn, ERP_MONTHLY_SERIES, cutoff, kt)
    erp_annual = _macro_at(conn, ERP_SERIES, cutoff, kt)
    erp_basis = "monthly ERP (T12m)"
    if erp is None:
        erp, erp_basis = erp_annual, "annual Implied ERP (FCFE) (no monthly value available)"
        out["flags"].append("monthly ERP not available at the cutoff: annual year-end value used")
    elif not erp["availability_verified"]:
        out["flags"].append(f"ERP for {erp['obs_date'][:7]}: publication date assumed ({erp['availability_basis']})")
    aaa = _macro_at(conn, "FRED:AAA", cutoff, kt)
    bei = _macro_at(conn, "FRED:T10YIE", cutoff, kt)
    for name, v in (("price", bar), ("10y Treasury", rf), ("Damodaran ERP", erp), ("Aaa yield", aaa),
                    ("10y breakeven", bei)):
        if v is None:
            out["missing"].append(name)
    beta = beta_at(conn, cid, cutoff, kt)
    if beta is None:
        out["missing"].append("beta (insufficient price history)")
    if out["missing"]:
        return out
    splits = corporate_actions(conn, cid, kt, "split")
    px = as_traded_close(bar, splits)[0]
    shares = sh * split_factor_between(splits, sh_avail[:10], bar["trade_date"])
    eff_tax = tax / pre if pre else None
    tau = min(max(eff_tax, TAX_BOUNDS[0]), TAX_BOUNDS[1]) if eff_tax is not None else 0.21
    if eff_tax is not None and eff_tax != tau:
        out["flags"].append(f"effective tax rate {round(eff_tax, 4)} ({tax_basis}) bounded to {tau}")
    out.update({
        "price": px, "price_date": bar["trade_date"], "shares": shares, "shares_basis": sh_basis,
        "market_cap": px * shares, "revenue_ttm": rev, "ebit_ttm": ebit, "cfo_ttm": cfo, "capex_ttm": capex,
        "depreciation_ttm": dep, "sbc_ttm": sbc, "interest_ttm": interest, "pretax_ttm": pre, "tax_ttm": tax,
        "effective_tax": eff_tax, "tax_rate_used": tau, "cash_st": cash_st, "debt": debt, "finance_leases": leases,
        "revenue_ttm_3y_ago": rev3, "net_capex_3y": net_capex_3y, "revenue_added_3y": rev_add_3y,
        "sales_to_capital": (rev_add_3y / net_capex_3y) if net_capex_3y and net_capex_3y > 0 and rev_add_3y else None,
        "sales_to_capital_years": s2c_years,
        "rf": rf["value"] / 100, "rf_date": rf["obs_date"], "erp": erp["value"], "erp_year": erp["obs_date"][:4],
        "erp_date": erp["obs_date"], "erp_basis": erp_basis, "erp_available_at": erp["available_at"],
        "erp_availability_verified": erp["availability_verified"], "erp_mobs_id": erp["mobs_id"],
        "erp_annual": erp_annual["value"] if erp_annual else None,
        "erp_annual_year": erp_annual["obs_date"][:4] if erp_annual else None,
        "rd": aaa["value"] / 100, "rd_month": aaa["obs_date"][:7], "breakeven": bei["value"] / 100,
        "beta": beta, "capex_to_cfo": capex / cfo if (cfo and capex) else None, "tax_basis": tax_basis,
        "trailing_3y_revenue_cagr": (rev / rev3) ** (1 / 3) - 1 if rev and rev3 else None,
    })
    if out["sales_to_capital"] is None:
        out["missing"].append("sales-to-capital: " + ("missing " + ", ".join(s2c_missing) +
                                                      (" (company-specific XBRL tag not in SEC companyfacts)"
                                                       if any("depreciation" in x for x in s2c_missing) else "")
                                                      if s2c_missing else "non-positive net capex over 3 years"))
    out["inputs_available_at"] = max([v["available_at"] for v in out["sources"].values()] +
                                     [bar["available_at"], rf["available_at"], erp["available_at"], aaa["available_at"],
                                      bei["available_at"], sh_avail])
    return out


def wacc(inp, beta_key="blume", erp=None, rf=None):
    rf = inp["rf"] if rf is None else rf
    erp = inp["erp"] if erp is None else erp
    ke = rf + inp["beta"][beta_key] * erp
    e, d = inp["market_cap"], inp["debt"] + inp["finance_leases"]
    kd = inp["rd"] * (1 - inp["tax_rate_used"])
    return {"ke": ke, "kd_after_tax": kd, "we": e / (e + d), "wd": d / (e + d), "wacc": e / (e + d) * ke + d / (e + d) * kd}


# ----------------------------------------------------------------------------------------- model
def growth_path(g, gt, years=YEARS, high=HIGH):
    return [g if t <= high else g + (gt - g) * (t - high) / (years - high) for t in range(1, years + 1)]


TRANSITIONS = ("fade", "abrupt")


def s2c_path(margin, tau, s2c, ronic, transition="fade", years=YEARS, high=HIGH):
    """Sales-to-capital by year. 'fade': incremental ROIC m(1-tau)S2C in years 1-5, then linear to RONIC by year 10.
    'abrupt' (Sprint 2.1): S2C constant to year 10, RONIC applies only from year 11."""
    after_tax = margin * (1 - tau)
    if transition == "abrupt" or after_tax <= 0:
        return [s2c] * years
    roic_f = after_tax * s2c
    return [s2c if t <= high else (roic_f + (ronic - roic_f) * (t - high) / (years - high)) / after_tax
            for t in range(1, years + 1)]


def enterprise_value(r0, g, margin, tau, s2c, w, gt, ronic, transition="fade"):
    if w <= gt:
        return None
    rev, ev, prev = r0, 0.0, r0
    sp = s2c_path(margin, tau, s2c, ronic, transition)
    for t, gr in enumerate(growth_path(g, gt), 1):
        rev = prev * (1 + gr)
        fcff = rev * margin * (1 - tau) - (rev - prev) / sp[t - 1]
        ev += fcff / (1 + w) ** t
        prev = rev
    nopat_11 = rev * (1 + gt) * margin * (1 - tau)
    tv = nopat_11 * (1 - gt / ronic) / (w - gt)
    return ev + tv / (1 + w) ** YEARS


def solve(f, target, lo=-0.5, hi=1.5, it=200):
    flo, fhi = f(lo), f(hi)
    if flo is None or fhi is None or (flo - target) * (fhi - target) > 0:
        return None
    for _ in range(it):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm - target) * (flo - target) > 0:
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def implied_growth(inp, w=None, gt=None, margin=None, s2c=None, ronic=None, tau=None, transition="fade"):
    w = w if w is not None else wacc(inp)["wacc"]
    gt = gt if gt is not None else min(inp["rf"], inp["breakeven"] + REAL_GROWTH)
    margin = margin if margin is not None else inp["ebit_ttm"] / inp["revenue_ttm"]
    s2c = s2c if s2c is not None else inp["sales_to_capital"]
    ronic = ronic if ronic is not None else w
    tau = tau if tau is not None else inp["tax_rate_used"]
    target_ev = inp["market_cap"] + inp["debt"] + inp["finance_leases"] - inp["cash_st"]
    g = solve(lambda x: enterprise_value(inp["revenue_ttm"], x, margin, tau, s2c, w, gt, ronic, transition), target_ev)
    return {"implied_revenue_cagr_y1_5": g, "wacc": w, "terminal_growth": gt, "margin": margin, "s2c": s2c,
            "ronic": ronic, "tax": tau, "target_ev": target_ev, "transition": transition,
            "roic_incremental_y1_5": margin * (1 - tau) * s2c,
            "s2c_path": s2c_path(margin, tau, s2c, ronic, transition)}


def roic_scenarios(inp):
    """Explicit alternatives for returns on new capital after year 5 (implied revenue growth, years 1-5)."""
    b = implied_growth(inp)
    w, roic_f = b["wacc"], b["roic_incremental_y1_5"]
    mid = w + 0.5 * (roic_f - w)
    sc = [("A. abrupt: ROIC_f to year 10, RONIC = WACC from year 11 (Sprint 2.1 base)", "abrupt", w),
          ("B. fade years 6-10 to RONIC = WACC (v2.1 base)", "fade", w),
          ("C. fade to half the forecast spread, RONIC = WACC + (ROIC_f - WACC)/2", "fade", mid),
          ("D. persistent: RONIC = ROIC_f forever (no fade)", "fade", roic_f)]
    return [{"scenario": name, "transition": tr, "terminal_ronic": r,
             "implied_revenue_cagr_y1_5": implied_growth(inp, ronic=r, transition=tr)["implied_revenue_cagr_y1_5"]}
            for name, tr, r in sc]


def sensitivities(inp):
    base = implied_growth(inp)
    w0, m0, s0 = base["wacc"], base["margin"], base["s2c"]
    grid = {}
    for dw in (-0.01, 0.0, 0.01):
        for gt in (0.025, 0.03, 0.035, 0.04):
            grid[f"wacc{dw:+.0%}|gT{gt:.1%}"] = implied_growth(inp, w=w0 + dw, gt=min(gt, w0 + dw - 0.005))[
                "implied_revenue_cagr_y1_5"]
    other = {
        "margin -5pt": implied_growth(inp, margin=m0 - 0.05)["implied_revenue_cagr_y1_5"],
        "margin +5pt": implied_growth(inp, margin=m0 + 0.05)["implied_revenue_cagr_y1_5"],
        "S2C x0.5 (capex intensity doubles)": implied_growth(inp, s2c=s0 * 0.5)["implied_revenue_cagr_y1_5"],
        "S2C x2 (capex intensity halves)": implied_growth(inp, s2c=s0 * 2)["implied_revenue_cagr_y1_5"],
        "RONIC 20% (growth creates value)": implied_growth(inp, ronic=0.20)["implied_revenue_cagr_y1_5"],
        "tax 21% statutory": implied_growth(inp, tau=0.21)["implied_revenue_cagr_y1_5"],
        "raw beta (no Blume)": implied_growth(inp, w=wacc(inp, "raw")["wacc"])["implied_revenue_cagr_y1_5"],
    }
    if inp.get("erp_annual") is not None:
        other["ERP: annual year-end value (Sprint 2.1 input)"] = implied_growth(
            inp, w=wacc(inp, erp=inp["erp_annual"])["wacc"])["implied_revenue_cagr_y1_5"]
    return {"wacc_x_terminal": grid, "other": other, "roic_scenarios": roic_scenarios(inp)}


# ----------------------------------------------------------------------------------------- bridge from v1
def _fcf_growth(target, fcf0, r, gt):
    from .implied import solve_growth
    return solve_growth(target, fcf0, r, gt)


def bridge(inp):
    """Returns [] when the v1 inputs (TTM CFO, capex, SBC) are not available.
    Implied growth after each methodological change (v1 -> v2). Steps 0-3 are implied FCF growth (10y constant);
    steps 4-5 are implied revenue growth (years 1-5, fade), because the cash-flow base changes."""
    if None in (inp.get("cfo_ttm"), inp.get("capex_ttm"), inp.get("sbc_ttm")):
        return []
    mcap, rf = inp["market_cap"], inp["rf"]
    ann = dict(inp, erp=inp["erp_annual"]) if inp.get("erp_annual") is not None else inp   # steps 0-5: Sprint 2.1 ERP
    w = wacc(ann)
    fcfe = inp["cfo_ttm"] - inp["capex_ttm"]
    fcff = fcfe - inp["sbc_ttm"] + (inp["interest_ttm"] or 0) * (1 - inp["tax_rate_used"])
    ev_target = mcap + inp["debt"] + inp["finance_leases"] - inp["cash_st"]
    steps = [
        ("0. v1: FCFE = CFO - capex, equity value, r = r_f + 5% (no beta), terminal 3%", _fcf_growth(mcap, fcfe, rf + 0.05, 0.03)),
        ("1. cost of equity = r_f + beta x Damodaran ERP (CAPM)", _fcf_growth(mcap, fcfe, w["ke"], 0.03)),
        ("2. treat stock-based compensation as a cost (CFO adds it back)", _fcf_growth(mcap, fcfe - inp["sbc_ttm"], w["ke"], 0.03)),
        ("3. enterprise basis: FCFF at WACC vs EV (net cash, debt, leases)", _fcf_growth(ev_target, fcff, w["wacc"], 0.03)),
    ]
    base_gt3 = implied_growth(ann, w=w["wacc"], gt=0.03, ronic=10.0, transition="abrupt")[
        "implied_revenue_cagr_y1_5"]                                                     # ronic huge: no terminal reinvestment
    steps.append(("4. normalise capex: NOPAT less growth-linked reinvestment (S2C); metric = revenue growth y1-5", base_gt3))
    steps.append(("5. terminal: g_T = min(r_f, breakeven + 1%) with reinvestment g_T/RONIC, RONIC = WACC (Sprint 2.1 base)",
                  implied_growth(ann, w=w["wacc"], transition="abrupt")["implied_revenue_cagr_y1_5"]))
    steps.append(("6. ERP: latest point-in-time monthly 'ERP (T12m)' instead of the last year-end value",
                  implied_growth(inp, transition="abrupt")["implied_revenue_cagr_y1_5"]))
    steps.append(("7. incremental ROIC fades from m(1-tau)S2C to RONIC = WACC over years 6-10 (v2.1 base)",
                  implied_growth(inp)["implied_revenue_cagr_y1_5"]))
    return steps


# ----------------------------------------------------------------------------------------- historical series
def ingest_history(conn, run_id, stats, kt) -> int:
    """v2 implied growth just before and after every earnings release since 2018 (price-only revision isolates the
    market's change with fundamentals frozen). Results with missing essentials are recorded as not computed."""
    from datetime import date as _date
    from ..db import companies
    from ..expectations import event_key_for
    from ..reaction import PriceSeries, classify_timing
    from .store import put
    spy = conn.execute("SELECT company_id FROM company WHERE ticker='SPY'").fetchone()[0]
    cal = PriceSeries(conn, spy, kt).dates
    n, skipped = 0, {}
    for c in companies(conn):
        for ev in conn.execute("SELECT * FROM event WHERE company_id=? AND event_type='earnings_release' AND "
                               "event_date>='2018-01-01' ORDER BY available_at", (c["company_id"],)).fetchall():
            d0, timing, _ = classify_timing(ev["available_at"], cal)
            if d0 is None or cal.index(d0) + 1 >= len(cal):
                continue
            key = event_key_for(conn, ev).split(":")[1]
            pre_cut = min(et_to_utc_str(_date.fromisoformat(cal[cal.index(d0) - 1]), 16, 1), ev["available_at"])
            post_cut = et_to_utc_str(_date.fromisoformat(d0), 16, 1)
            for tag, cut, fcut in (("pre", pre_cut, None), ("post", post_cut, None),
                                   ("post_same_fundamentals", post_cut, pre_cut)):
                inp = inputs_at(conn, c, cut, kt, fcut)
                if inp["missing"]:
                    skipped[f"{c['ticker']}:{key}:{tag}"] = inp["missing"]
                    continue
                res = implied_growth(inp)
                w = wacc(inp)
                n += put(conn, company_id=c["company_id"], cls="market_implied", provider="reverse DCF v2.1 (model)",
                         metric="implied_revenue_cagr_y1_5",
                         basis="FCFF/EV, CAPM WACC (monthly ERP), S2C reinvestment, ROIC fade to WACC, fade to g_T",
                         target_period=key, stat=tag, value=res["implied_revenue_cagr_y1_5"], unit="ratio",
                         as_of=inp["inputs_available_at"], as_of_basis="computed from point-in-time inputs",
                         available_at=inp["inputs_available_at"], grade="B", method="reverse_dcf_v2_1",
                         checks={"wacc": w, "terminal_growth": res["terminal_growth"], "margin": res["margin"],
                                 "s2c": res["s2c"], "flags": inp["flags"], "shares_basis": inp["shares_basis"],
                                 "beta": inp["beta"]["blume"], "erp": inp["erp"], "erp_date": inp["erp_date"],
                                 "rf": inp["rf"], "roic_incremental_y1_5": res["roic_incremental_y1_5"]},
                         notes=None if res["implied_revenue_cagr_y1_5"] is not None else
                         "no growth rate in [-50%, 150%] reproduces the price",
                         natural_key=f"rdcf21|{c['ticker']}|{ev['accession_no']}|{tag}", run_id=run_id)
    stats["implied_growth_v2_new"] = n
    stats["implied_growth_v2_not_computed"] = len(skipped)
    stats["implied_growth_v2_not_computed_reasons"] = sorted({m for v in skipped.values() for m in v})
    return n


# ----------------------------------------------------------------------------------------- ERP vintage audit
def annual_erp_vintages(store, run_id=None):
    """Audit of the annual series (histimpl.xls) against its own archived vintages: for each year-end, the first
    capture showing it, its value then, and any later change. Evidence is stored; no observation is written."""
    from . import archive
    first, changes, caps = {}, [], []
    for cap in archive.list_captures(store, DAMODARAN_URL.split("://")[1], start="2016", end="2026", run_id=run_id):
        doc_id, body = archive.fetch_capture(store, cap, run_id=run_id, title="Damodaran histimpl.xls (archived)")
        if not body:
            continue
        try:
            rows = dict(parse_damodaran_xls(body))
        except Exception:
            caps.append({"served_at": cap.get("served_at"), "doc_id": doc_id, "parsed": False})
            continue
        caps.append({"served_at": cap["served_at"], "doc_id": doc_id, "parsed": True, "last_year": max(rows)})
        for y, v in rows.items():
            if y not in first:
                first[y] = {"first_seen": cap["served_at"], "value": v, "doc_id": doc_id}
            elif abs(first[y]["value"] - v) > 1e-9 and not any(c["year"] == y and c["to"] == v for c in changes):
                changes.append({"year": y, "from": first[y]["value"], "to": v, "seen_at": cap["served_at"]})
    return {"captures": caps, "first_seen": first, "revisions": changes}
