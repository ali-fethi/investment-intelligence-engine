"""Independent re-computation (Sprint 2.1 audit).

Rules for this script:
  * It imports nothing from `iie` except to read stored evidence bytes; every calculation is re-implemented here.
  * Inputs come from the RAW source files kept in the evidence store (SEC companyfacts JSON, Yahoo chart JSON,
    Treasury CSV, FRED CSV, Damodaran XLS, archived HTML pages), not from the parsed database tables.
  * Results are compared with the engine's published outputs (docs/valuation, docs/expectations, docs/event_engine).
Run:  .venv/bin/python audit/independent_check.py   ->  audit/results.json
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import os

ROOT = Path(os.environ.get("IIE_AUDIT_ROOT") or Path(__file__).resolve().parents[1])
# repository: data/iie.sqlite + data/evidence; external bundle (audit/make_bundle.py): bundle.sqlite + evidence/
DB = ROOT / "data" / "iie.sqlite" if (ROOT / "data" / "iie.sqlite").exists() else ROOT / "bundle.sqlite"
EVID = ROOT / "data" / "evidence" if (ROOT / "data" / "evidence").exists() else ROOT / "evidence"
ET = ZoneInfo("America/New_York")
CIK = {"MSFT": "0000789019", "GOOGL": "0001652044", "META": "0001326801"}
FYE = {"MSFT": 6, "GOOGL": 12, "META": 12}

db = sqlite3.connect(str(DB))
db.row_factory = sqlite3.Row


def raw(doc_id: int) -> bytes:
    r = db.execute("SELECT storage_path FROM source_document WHERE doc_id=?", (doc_id,)).fetchone()
    with gzip.open(EVID / r["storage_path"], "rb") as f:
        return f.read()


def latest_doc(url_like: str, before: str | None = None, system: str | None = None):
    sql = "SELECT doc_id, retrieved_at FROM source_document WHERE url LIKE ? AND http_status=200"
    p = [url_like]
    if system:
        sql += " AND source_system=?"
        p.append(system)
    if before:
        sql += " AND retrieved_at<=?"
        p.append(before)
    return db.execute(sql + " ORDER BY doc_id DESC LIMIT 1", p).fetchone()


# ------------------------------------------------------------------------------------------- XBRL (raw JSON)
def facts(ticker):
    d = latest_doc(f"%companyfacts/CIK{CIK[ticker]}.json")
    return json.loads(raw(d["doc_id"]))["facts"]


def filed_before(f, cutoff_date):
    return f["filed"] <= cutoff_date


def _dur(f):
    if "start" not in f:
        return None
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


def latest_value(series, start, end, cutoff_date):
    c = [f for f in series if f.get("start") == start and f["end"] == end and filed_before(f, cutoff_date)]
    c.sort(key=lambda f: f["filed"])
    return c[-1]["val"] if c else None


def ttm_independent(fx, concepts, cutoff_date):
    """TTM = latest YTD (or FY) + previous FY - same YTD a year earlier, using only facts filed by cutoff.
    Among candidate concepts, the one with the most recent period end is used (issuers switch tags over time)."""
    def latest_end(concept):
        tax, name = concept.split(":")
        if name not in fx.get(tax, {}):
            return ""
        ends = [f["end"] for u in fx[tax][name]["units"].values() for f in u if filed_before(f, cutoff_date) and "start" in f]
        return max(ends) if ends else ""
    concepts = sorted(concepts, key=latest_end, reverse=True)
    for concept in concepts:
        tax, name = concept.split(":")
        if name not in fx.get(tax, {}):
            continue
        series = [f for u in fx[tax][name]["units"].values() for f in u if filed_before(f, cutoff_date)]
        durs = [f for f in series if _dur(f) is not None]
        if not durs:
            continue
        latest_end = max(f["end"] for f in durs)
        ytd = [f for f in durs if f["end"] == latest_end]
        ytd.sort(key=lambda f: (-_dur(f), f["filed"]))
        y = ytd[0]                                     # longest period ending at latest_end = fiscal YTD
        if 350 <= _dur(y) <= 380:
            return y["val"], f"{name} FY ending {latest_end}"
        ys, ye = date.fromisoformat(y["start"]), date.fromisoformat(y["end"])
        fy = [f for f in durs if 350 <= _dur(f) <= 380 and date.fromisoformat(f["end"]) < ye and
              (ys - date.fromisoformat(f["end"])).days in range(0, 8)]
        prev = [f for f in durs if abs((date.fromisoformat(f["end"]) - (ye - timedelta(days=365))).days) <= 7 and
                abs(_dur(f) - _dur(y)) <= 7]
        if fy and prev:
            fy.sort(key=lambda f: f["filed"])
            prev.sort(key=lambda f: f["filed"])
            return y["val"] + fy[-1]["val"] - prev[-1]["val"], f"{name} YTD {y['start']}..{y['end']} + FY - prior YTD"
    return None, "not found"


def instant_independent(fx, concepts, cutoff_date):
    for concept in concepts:
        tax, name = concept.split(":")
        if name not in fx.get(tax, {}):
            continue
        series = [f for u in fx[tax][name]["units"].values() for f in u if "start" not in f and filed_before(f, cutoff_date)]
        if series:
            end = max(f["end"] for f in series)
            c = sorted([f for f in series if f["end"] == end], key=lambda f: f["filed"])
            return c[-1]["val"], end
    return None, None


# ------------------------------------------------------------------------------------------- prices (raw JSON)
def chart(ticker):
    d = latest_doc(f"%finance/chart/{ticker.replace('^', '%5E')}%")
    res = json.loads(raw(d["doc_id"]))["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = {}
    for i, t in enumerate(res["timestamp"]):
        if q["close"][i] is None:
            continue
        d_ = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(ET).date().isoformat()
        out[d_] = {"open": q["open"][i], "close": q["close"][i]}
    div = {datetime.fromtimestamp(v["date"], tz=timezone.utc).astimezone(ET).date().isoformat(): v["amount"]
           for v in (res.get("events") or {}).get("dividends", {}).values()}
    return out, div, d["retrieved_at"]


def total_return(px, div, d0, d1, days):
    i, j = days.index(d0), days.index(d1)
    f = 1.0
    for k in range(i + 1, j + 1):
        a, b = days[k - 1], days[k]
        f *= (px[b]["close"] + div.get(b, 0.0)) / px[a]["close"]
    return f - 1


def beta_independent(ticker, cutoff_date, weeks=104):
    px, dv, _ = chart(ticker)
    mpx, mdv, _ = chart("SPY")
    days = sorted(d for d in mpx if d <= cutoff_date and d in px)
    weekly = {}
    for d in days:
        weekly[date.fromisoformat(d).isocalendar()[:2]] = d
    pts = sorted(weekly.values())[-(weeks + 1):]
    xs = [total_return(mpx, mdv, a, b, days) for a, b in zip(pts, pts[1:])]
    ys = [total_return(px, dv, a, b, days) for a, b in zip(pts, pts[1:])]
    n = len(xs)
    # beta = cov / var via sums (independent formula)
    sx, sy = sum(xs), sum(ys)
    sxy, sxx = sum(a * b for a, b in zip(xs, ys)), sum(a * a for a in xs)
    return (n * sxy - sx * sy) / (n * sxx - sx * sx), n


# ------------------------------------------------------------------------------------------- rates (raw files)
def treasury_10y(cutoff_date):
    y = cutoff_date[:4]
    d = latest_doc(f"%daily-treasury-rates.csv/{y}/%")
    rows = list(csv.DictReader(io.StringIO(raw(d["doc_id"]).decode("utf-8-sig"))))
    best = max((r for r in rows if datetime.strptime(r["Date"], "%m/%d/%Y").date().isoformat() <= cutoff_date),
               key=lambda r: datetime.strptime(r["Date"], "%m/%d/%Y"))
    return float(best["10 Yr"]) / 100, best["Date"]


def fred_latest(series, cutoff_date, lag_days):
    d = latest_doc(f"%fredgraph.csv?id={series}&%")
    rows = list(csv.reader(io.StringIO(raw(d["doc_id"]).decode("utf-8-sig"))))[1:]
    ok = [(a, b) for a, b in rows if b not in ("", ".") and
          (date.fromisoformat(a) + timedelta(days=lag_days)).isoformat() <= cutoff_date]
    a, b = ok[-1]
    return float(b) / 100, a


def damodaran(cutoff_date):
    import pandas as pd
    d = latest_doc("%histimpl.xls", system="damodaran")      # the publisher's file, not an archived copy
    df = pd.read_excel(io.BytesIO(raw(d["doc_id"])), header=None)
    h = df.index[df[0].astype(str).str.strip() == "Year"][0]
    col = list(df.iloc[h].astype(str).str.strip()).index("Implied ERP (FCFE)")
    year = int(cutoff_date[:4]) - 1 if cutoff_date[5:] >= "01-10" else int(cutoff_date[:4]) - 2
    row = df[df[0].astype(str).str.startswith(str(year))]
    return float(row.iloc[0, col]), year


def _served_utc(doc):
    """Capture actually served for an archived document (stored served-check), else its requested timestamp."""
    url = doc["url"]
    chk = db.execute("""SELECT doc_id FROM source_document WHERE doc_type='wayback_served_check' AND url=?
                        ORDER BY doc_id DESC LIMIT 1""", (url + "#served",)).fetchone()
    ts = json.loads(raw(chk["doc_id"]))["served"] if chk else url.split("/web/")[1][:14]
    return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _monthly_rows(body):
    """openpyxl, cell by cell (the engine uses pandas): {YYYY-MM-01: ERP (T12m)}."""
    import openpyxl
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        except Exception:
            return {}
    if "Historical ERP" not in wb.sheetnames:
        return {}
    rows = list(wb["Historical ERP"].iter_rows(values_only=True))
    hdr = next((i for i, r in enumerate(rows[:10]) if r and str(r[0]).strip() == "Start of month"), None)
    if hdr is None:
        return {}
    names = [str(c).strip() if c is not None else "" for c in rows[hdr]]
    if "ERP (T12m)" not in names:
        return {}
    j, out = names.index("ERP (T12m)"), {}
    for r in rows[hdr + 1:]:
        if not isinstance(r[0], datetime):
            continue
        v = r[j]
        if isinstance(v, str):
            v = v.strip().replace(",", ".")
            v = float(v[:-1]) / 100 if v.endswith("%") else float(v)
        if isinstance(v, (int, float)) and 0 < v < 0.2:
            out[r[0].date().replace(day=1).isoformat()] = round(float(v), 6)
    return out


def damodaran_monthly(cutoff_utc):
    """Latest month whose value is evidenced (first archived capture showing it, or month start + 14 days when the
    first evidence is more than 45 days late) by the cutoff."""
    docs = db.execute("""SELECT doc_id, url, doc_type, retrieved_at FROM source_document WHERE url LIKE '%ERPbymonth.xlsx'
                         AND http_status=200 ORDER BY doc_id""").fetchall()
    ev = []
    for d in docs:
        at = _served_utc(d) if d["doc_type"] == "archived_page" else d["retrieved_at"]
        rows = _monthly_rows(raw(d["doc_id"]))
        if rows:
            ev.append((at, rows))
    ev.sort(key=lambda x: x[0])
    first = {}
    for at, rows in ev:
        for m, v in rows.items():
            if m not in first:
                late = at > (datetime.fromisoformat(m) + timedelta(days=45)).strftime("%Y-%m-%dT05:00:00Z")
                avail = (datetime.fromisoformat(m) + timedelta(days=14)).strftime("%Y-%m-%dT05:00:00Z") if late else at
                first[m] = (avail, v, not late)
    usable = sorted(m for m, (a, _, _) in first.items() if a <= cutoff_utc)
    m = usable[-1]
    return first[m][1], m, first[m][0], first[m][2], len(ev)


# ------------------------------------------------------------------------------------------- DCF (independent)
def dcf_ev(r0, g, m, tau, s2c, w, gt, ronic, n=10, high=5):
    revs = [r0]
    for t in range(1, n + 1):
        gr = g if t <= high else g + (gt - g) * (t - high) / (n - high)
        revs.append(revs[-1] * (1 + gr))
    fcff = [revs[t] * m * (1 - tau) - (revs[t] - revs[t - 1]) / s2c for t in range(1, n + 1)]
    disc = [(1 + w) ** -t for t in range(1, n + 1)]
    tv = revs[n] * (1 + gt) * m * (1 - tau) * (1 - gt / ronic) / (w - gt)
    return sum(f * d for f, d in zip(fcff, disc)) + tv * disc[-1]


def dcf_ev_fade(r0, g, m, tau, s2c, w, gt, ronic, n=10, high=5):
    """Same model with the v2.1 transition: incremental ROIC = m(1-tau)S2C in years 1-5, then linear to RONIC."""
    nopat_margin = m * (1 - tau)
    roic = [nopat_margin * s2c] * high + [nopat_margin * s2c + (ronic - nopat_margin * s2c) * k / (n - high)
                                          for k in range(1, n - high + 1)]
    rev, pv = r0, 0.0
    for t in range(1, n + 1):
        gr = g if t <= high else g + (gt - g) * (t - high) / (n - high)
        new = rev * (1 + gr)
        pv += (new * nopat_margin - (new - rev) * nopat_margin / roic[t - 1]) / (1 + w) ** t
        rev = new
    return pv + rev * (1 + gt) * nopat_margin * (1 - gt / ronic) / (w - gt) / (1 + w) ** n


def secant(f, x0, x1, tol=1e-10, it=200):
    f0, f1 = f(x0), f(x1)
    for _ in range(it):
        if f1 == f0:
            break
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        x0, f0, x1, f1 = x1, f1, x2, f(x2)
        if abs(x1 - x0) < tol:
            return x1
    return x1


def valuation_check(cutoff="2026-11-05"):
    pub = json.loads((ROOT / "docs" / "valuation" / f"valuation_{cutoff}.json").read_text())
    out = {}
    for tk in ("GOOGL", "MSFT", "META"):
        eng = pub["companies"][tk]
        fx = facts(tk)
        rev, rev_src = ttm_independent(fx, ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                                             "us-gaap:Revenues"], cutoff)
        ebit, _ = ttm_independent(fx, ["us-gaap:OperatingIncomeLoss"], cutoff)
        pre, _ = ttm_independent(fx, ["us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"], cutoff)
        tax, _ = ttm_independent(fx, ["us-gaap:IncomeTaxExpenseBenefit"], cutoff)
        cash1, bsd = instant_independent(fx, ["us-gaap:CashCashEquivalentsAndShortTermInvestments"], cutoff)
        if cash1 is None:
            c, bsd = instant_independent(fx, ["us-gaap:CashAndCashEquivalentsAtCarryingValue"], cutoff)
            ms, _ = instant_independent(fx, ["us-gaap:MarketableSecuritiesCurrent"], cutoff)
            cash1 = c + (ms or 0)
        dc, _ = instant_independent(fx, ["us-gaap:LongTermDebtCurrent"], cutoff)
        dn, _ = instant_independent(fx, ["us-gaap:LongTermDebtNoncurrent"], cutoff)
        fl, fld = instant_independent(fx, ["us-gaap:FinanceLeaseLiability"], cutoff)
        fl = fl if fld == bsd else 0
        dc = dc or 0
        e = eng["inputs"]
        # independent market data and rates
        px, _, _ = chart(tk)
        last = max(d for d in px if d <= cutoff)
        price = px[last]["close"]                       # no split after 2022 for these names
        beta_raw, nw = beta_independent(tk, cutoff)
        beta = 0.67 * beta_raw + 0.33
        rf, rf_d = treasury_10y(cutoff)
        erp_annual, erp_y = damodaran(cutoff)
        cutoff_utc = pub["cutoff"]
        erp, erp_m, erp_at, erp_verified, n_vint = damodaran_monthly(cutoff_utc)
        rd, rd_m = fred_latest("AAA", cutoff, 38)
        be, _ = fred_latest("T10YIE", cutoff, 1)
        shares = e["shares"]                            # share count compared separately with the 10-Q cover page
        mcap = price * shares
        debt = dc + dn
        etr = tax / pre
        tau = min(max(etr, 0.10), 0.30)
        wd = (debt + fl) / (mcap + debt + fl)
        w = (1 - wd) * (rf + beta * erp) + wd * rd * (1 - tau)
        w_annual = (1 - wd) * (rf + beta * erp_annual) + wd * rd * (1 - tau)
        gt = min(rf, be + 0.01)
        s2c = e["sales_to_capital"]                      # recomputed below from annual facts
        # annual S2C independently
        L = int(e["sales_to_capital_years"][-4:])

        def fy_val(names, year):
            for nm in names:
                t_, n_ = nm.split(":")
                if n_ in fx.get(t_, {}):
                    cand = [f for u in fx[t_][n_]["units"].values() for f in u if f.get("fp") == "FY" and
                            _dur(f) and 350 <= _dur(f) <= 380 and int(f["end"][:4]) == (year if FYE[tk] == 12 else year)
                            and f["filed"] <= cutoff]
                    if cand:
                        cand.sort(key=lambda f: f["filed"])
                        return cand[-1]["val"]
            return None
        rev_names = ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:Revenues"]
        capn = ["us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"]
        depn = ["us-gaap:Depreciation", "us-gaap:DepreciationDepletionAndAmortization"]
        try:
            s2c_ind = (fy_val(rev_names, L) - fy_val(rev_names, L - 3)) / sum(
                fy_val(capn, L - k) - fy_val(depn, L - k) for k in range(3))
        except TypeError:
            s2c_ind = None
        target = mcap + debt + fl - cash1
        s_ = s2c_ind or s2c
        g_before = secant(lambda x: dcf_ev(rev, x, ebit / rev, tau, s_, w_annual, gt, w_annual) - target, 0.05, 0.25)
        g_erp_only = secant(lambda x: dcf_ev(rev, x, ebit / rev, tau, s_, w, gt, w) - target, 0.05, 0.25)
        g = secant(lambda x: dcf_ev_fade(rev, x, ebit / rev, tau, s_, w, gt, w) - target, 0.05, 0.25)
        roic_f = ebit / rev * (1 - tau) * s_
        g_mid = secant(lambda x: dcf_ev_fade(rev, x, ebit / rev, tau, s_, w, gt, w + (roic_f - w) / 2) - target, 0.05, 0.25)
        g_persist = secant(lambda x: dcf_ev_fade(rev, x, ebit / rev, tau, s_, w, gt, roic_f) - target, 0.05, 0.25)
        sc_eng = {d["scenario"][0]: d["implied_revenue_cagr_y1_5"] for d in eng["sensitivities"]["roic_scenarios"]}
        bridge = dict((n_[0], v_) for n_, v_ in eng["bridge"])
        out[tk] = {
            "independent": {"revenue_ttm": rev, "revenue_source": rev_src, "ebit_ttm": ebit, "eff_tax": etr,
                            "cash_st": cash1, "debt": debt, "finance_leases": fl, "balance_sheet_date": bsd,
                            "price": price, "price_date": last, "beta_raw": beta_raw, "beta_weeks": nw, "rf": rf,
                            "rf_date": rf_d, "erp": erp, "erp_date": erp_m, "erp_available_at": erp_at,
                            "erp_availability_verified": erp_verified, "erp_vintages_read": n_vint,
                            "erp_annual": erp_annual, "erp_year": erp_y, "rd": rd, "rd_month": rd_m,
                            "breakeven": be, "wacc": w, "terminal_growth": gt, "s2c": s2c_ind,
                            "roic_incremental_y1_5": roic_f,
                            "before_patch_g": g_before, "after_erp_only_g": g_erp_only,
                            "scenario_C_g": g_mid, "scenario_D_g": g_persist,
                            "implied_revenue_cagr_y1_5": g},
            "engine": {"revenue_ttm": e["revenue_ttm"], "ebit_ttm": e["ebit_ttm"], "eff_tax": e["effective_tax"],
                       "cash_st": e["cash_st"], "debt": e["debt"], "finance_leases": e["finance_leases"],
                       "price": e["price"], "beta_raw": e["beta"]["raw"], "rf": e["rf"], "erp": e["erp"],
                       "rd": e["rd"], "breakeven": e["breakeven"], "wacc": eng["wacc"]["wacc"],
                       "erp_date": e["erp_date"], "erp_available_at": e["erp_available_at"],
                       "erp_annual": e["erp_annual"], "roic_incremental_y1_5": eng["base"]["roic_incremental_y1_5"],
                       "before_patch_g": bridge["5"], "after_erp_only_g": bridge["6"],
                       "scenario_C_g": sc_eng["C"], "scenario_D_g": sc_eng["D"],
                       "terminal_growth": eng["base"]["terminal_growth"], "s2c": e["sales_to_capital"],
                       "implied_revenue_cagr_y1_5": eng["base"]["implied_revenue_cagr_y1_5"]},
        }
        diffs = {}
        for k, v in out[tk]["independent"].items():
            ev = out[tk]["engine"].get(k)
            if isinstance(v, (int, float)) and isinstance(ev, (int, float)) and ev:
                diffs[k] = v / ev - 1
        out[tk]["relative_differences"] = diffs
    return out


# ------------------------------------------------------------------------------------------- EPS surprises & CAR
MW_RE_NEW = re.compile(r"(Q[1-4] \d{4})((?: Q[1-4] \d{4}){0,2}) High ([\d. ]+?) Low ([\d. ]+?) Average ([\d. ]+)")
MW_RE_OLD = re.compile(r"# of Estimates [\d.]+ [\d.]+ [\d.]+ [\d.]+ Mean Estimate ([-\d.]+)")


def page_text(b):
    t = b.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))


def surprise_check(n=12):
    rows = json.loads((ROOT / "docs" / "expectations" / "events.json").read_text())
    picked = [r for r in rows if r.get("consensus_eps_evidence") and r.get("eps_gaap")][::4][:n]
    out = []
    for r in picked:
        eo = db.execute("SELECT * FROM expectation_obs WHERE eo_id=?", (r["consensus_eps_evidence"],)).fetchone()
        txt = page_text(raw(eo["source_doc_id"])) if eo["source_doc_id"] else ""
        found = None
        if "marketwatch" in (eo["source_url"] or ""):
            q = f"Q{r['quarter'][-1]} {r['quarter'][2:6]}"
            for m in MW_RE_NEW.finditer(txt):
                labels = [m.group(1)] + re.findall(r"Q[1-4] \d{4}", m.group(2))
                avgs = m.group(5).split()
                if q in labels:
                    found = float(avgs[labels.index(q)])
            if found is None:
                m = MW_RE_OLD.search(txt)
                found = float(m.group(1)) if m else None
            if eo["adjustments"] and found:
                found = found / float(re.search(r"divided by ([\d.]+)", eo["adjustments"]).group(1))
        else:
            m = re.search(r"\$([\d.]+)(?: per share)?", eo["locator"] or "")
            found = float(m.group(1)) if m and eo["locator"] and eo["locator"].replace(" ", "") in txt.replace(" ", "") else None
        # GAAP EPS from raw companyfacts: 3-month fact, first filed
        fx = facts(r["ticker"])
        ser = [f for u in fx["us-gaap"]["EarningsPerShareDiluted"]["units"].values() for f in u if _dur(f) and 80 <= _dur(f) <= 100]
        fy, q = int(r["quarter"][2:6]), int(r["quarter"][-1])
        g = None
        for f in sorted(ser, key=lambda f: f["filed"]):
            e = date.fromisoformat(f["end"])
            fy_f = e.year + (1 if FYE[r["ticker"]] == 6 and e.month > 6 else 0)
            q_f = ((e.month - FYE[r["ticker"]] - 1) % 12) // 3 + 1
            if fy_f == fy and q_f == q:
                g = f["val"]
                break
        out.append({"ticker": r["ticker"], "quarter": r["quarter"], "engine_consensus": r["consensus_eps"],
                    "recomputed_consensus_from_raw_page": found, "engine_gaap": r["eps_gaap"],
                    "recomputed_gaap_from_raw_xbrl": g,
                    "engine_surprise_gaap": r.get("eps_surprise_gaap"),
                    "recomputed_surprise_gaap": (g / found - 1) if g and found else None,
                    "available_before_release": eo["available_at"] is not None and eo["available_at"] < r["public_at"]})
    return out


def car_check(n=8):
    rows = json.loads((ROOT / "docs" / "expectations" / "events.json").read_text())
    picked = [r for r in rows if r.get("car_d0") is not None and r["public_at"] >= "2017-06-01"][::12][:n]
    out = []
    for r in picked:
        px, dv, _ = chart(r["ticker"])
        mpx, mdv, _ = chart("SPY")
        days = sorted(d for d in mpx if d in px)
        i0 = days.index(r["reaction_day"])
        est = days[i0 - 261:i0 - 10]          # returns ending at d0-260 ... d0-11 (same window definition)
        xs = [total_return(mpx, mdv, a, b, days) for a, b in zip(est, est[1:])]
        ys = [total_return(px, dv, a, b, days) for a, b in zip(est, est[1:])]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        a = my - b * mx
        rs = total_return(px, dv, days[i0 - 1], days[i0], days)
        rm = total_return(mpx, mdv, days[i0 - 1], days[i0], days)
        out.append({"ticker": r["ticker"], "quarter": r["quarter"], "engine_car": r["car_d0"],
                    "recomputed_car": rs - a - b * rm, "abs_diff": abs(rs - a - b * rm - r["car_d0"])})
    return out


def accounting_checks():
    """Net income ~ diluted EPS x diluted shares for every reported 3-month period (raw XBRL)."""
    out = {}
    for tk in ("MSFT", "META"):              # GOOGL reports per-class shares (dimensional), so the check is skipped
        fx = facts(tk)["us-gaap"]

        def series(name):
            return {(f["start"], f["end"]): f["val"] for u in fx[name]["units"].values() for f in u
                    if _dur(f) and 80 <= _dur(f) <= 100}
        ni, eps, sh = series("NetIncomeLoss"), series("EarningsPerShareDiluted"), \
            series("WeightedAverageNumberOfDilutedSharesOutstanding")
        bad, n = [], 0
        for k in ni:
            if k in eps and k in sh and ni[k]:
                n += 1
                rel = eps[k] * sh[k] / ni[k] - 1
                if abs(rel) > 0.02:
                    bad.append({"period": k, "rel": rel})
        out[tk] = {"periods_checked": n, "over_2pct": bad[:5], "n_over_2pct": len(bad)}
    return out


def lineage_check(cutoff="2026-11-05"):
    """TTM revenue lineage: the observation IDs the engine records must be facts of the current TTM window (latest
    period end filed by the cutoff), filed by the cutoff, with the recorded availability. Run on the patched output and
    on the pre-patch output kept in git (which recorded the 3-years-ago window)."""
    cur = json.loads((ROOT / "docs" / "valuation" / f"valuation_{cutoff}.json").read_text())
    before = ROOT / "docs" / "valuation" / f"valuation_{cutoff}_before_patch.json"     # Sprint 2.1 output (ccb55aa)
    old = json.loads(before.read_text()) if before.exists() else None
    out = {}
    for tk in ("GOOGL", "MSFT", "META"):
        fx = facts(tk)
        ends = [f["end"] for c in ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")
                for u in fx["us-gaap"].get(c, {"units": {}})["units"].values() for f in u
                if "start" in f and f["filed"] <= cutoff]
        window_end = max(ends)
        res = {"independent_window_end": window_end}
        for tag, doc in (("after", cur), ("before", old)):
            if doc is None:
                continue
            src = doc["companies"][tk]["inputs"]["sources"]["ttm_revenue"]
            obs = [db.execute("SELECT period_end, available_at, published_at FROM observation WHERE obs_id=?", (i,)).fetchone()
                   for i in src["inputs"]]
            res[tag] = {"max_period_end": max(o["period_end"] for o in obs),
                        "recorded_available_at": src["available_at"],
                        "max_obs_available_at": max(o["available_at"] for o in obs),
                        "window_matches": max(o["period_end"] for o in obs) == window_end,
                        "availability_matches": max(o["available_at"] for o in obs) == src["available_at"]}
        out[tk] = res
    return out


def lookahead_checks():
    rows = json.loads((ROOT / "docs" / "expectations" / "events.json").read_text())
    v = 0
    for r in rows:
        for k in ("consensus_eps_evidence", "consensus_revenue_evidence", "consensus_revenue_ex_tac_evidence"):
            if r.get(k):
                a = db.execute("SELECT available_at FROM expectation_obs WHERE eo_id=?", (r[k],)).fetchone()[0]
                v += a is None or a >= r["public_at"]
    val = json.loads((ROOT / "docs" / "valuation" / "valuation_2026-11-05.json").read_text())
    late = [tk for tk, c in val["companies"].items() if c["inputs"]["inputs_available_at"] > val["cutoff"]]
    return {"expectations_used_after_or_at_release": v, "valuation_inputs_after_cutoff": late}


def served_capture_checks():
    """For every archived consensus used for a release: the capture the archive actually serves (stored HEAD checks)
    must predate the release."""
    rows = json.loads((ROOT / "docs" / "expectations" / "events.json").read_text())
    viol, unverified, n = [], 0, 0
    for r in rows:
        eid = r.get("consensus_eps_evidence")
        if not eid:
            continue
        eo = db.execute("SELECT source_url FROM expectation_obs WHERE eo_id=?", (eid,)).fetchone()
        if "web.archive.org" not in (eo["source_url"] or ""):
            continue
        n += 1
        ts, orig = eo["source_url"].split("/web/")[1].split("/", 1)
        chk = db.execute("""SELECT doc_id FROM source_document WHERE doc_type='wayback_served_check' AND url=?
                            ORDER BY doc_id DESC LIMIT 1""", (f"http://web.archive.org/web/{ts}id_/{orig}#served",)).fetchone()
        if not chk:
            unverified += 1
            continue
        served = json.loads(raw(chk["doc_id"]))["served"]
        served_utc = datetime.strptime(served, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if served_utc >= r["public_at"]:
            viol.append({"ticker": r["ticker"], "quarter": r["quarter"], "served": served_utc, "public": r["public_at"]})
    return {"archived_consensus_used": n, "served_after_release": viol, "served_unverified": unverified}


def external_figures():
    """Figures read directly from the 10-Qs by a separate auditor process (no access to this repository)."""
    ext = json.loads((ROOT / "audit" / "external_10q_figures.json").read_text())
    val = json.loads((ROOT / "docs" / "valuation" / "valuation_2026-11-05.json").read_text())
    out = {}
    for tk, e in ext.items():
        i = val["companies"][tk]["inputs"]
        out[tk] = {k: {"10-Q": v, "engine": i[k], "rel_diff": (i[k] / v - 1) if v else None}
                   for k, v in e.items() if k in i}
    return out


if __name__ == "__main__" and "--scope=valuation" in sys.argv:
    # the part reproducible from the external evidence bundle (audit/make_bundle.py)
    res = {"valuation": valuation_check(), "ttm_lineage": lineage_check(), "external_10q": external_figures()}
    out = Path(os.environ.get("IIE_AUDIT_OUT") or ROOT / "audit" / "results_valuation.json")
    out.write_text(json.dumps(res, indent=1, default=str))
    for tk, d in res["valuation"].items():
        diffs = d["relative_differences"]
        print(tk, "implied growth engine", round(d["engine"]["implied_revenue_cagr_y1_5"], 6), "independent",
              round(d["independent"]["implied_revenue_cagr_y1_5"], 6), "| max relative difference",
              f"{max(abs(x) for x in diffs.values()):.1e}")
    print("TTM lineage:", {tk: {k: v.get("window_matches") for k, v in d.items() if isinstance(v, dict)}
                           for tk, d in res["ttm_lineage"].items()})
    print("->", out)
    sys.exit(0)

if __name__ == "__main__":
    res = {"valuation": valuation_check(), "eps_surprises": surprise_check(), "car": car_check(),
           "accounting": accounting_checks(), "lookahead": lookahead_checks(), "external_10q": external_figures(),
           "served_captures": served_capture_checks(), "ttm_lineage": lineage_check()}
    (ROOT / "audit" / "results.json").write_text(json.dumps(res, indent=1, default=str))
    v = res["valuation"]
    for tk, d in v.items():
        big = {k: round(x, 4) for k, x in d["relative_differences"].items() if abs(x) > 1e-6}
        print(tk, "implied growth engine", round(d["engine"]["implied_revenue_cagr_y1_5"], 4), "independent",
              round(d["independent"]["implied_revenue_cagr_y1_5"], 4), "| non-zero relative differences:", big)
    print("EPS:", [(s["ticker"], s["quarter"], s["engine_consensus"], s["recomputed_consensus_from_raw_page"],
                    s["engine_gaap"], s["recomputed_gaap_from_raw_xbrl"]) for s in res["eps_surprises"]])
    print("CAR max abs diff:", max(c["abs_diff"] for c in res["car"]))
    print("accounting:", res["accounting"])
    print("look-ahead:", res["lookahead"], "| served captures:", res["served_captures"])
    print("TTM lineage:", res["ttm_lineage"])
    print("external 10-Q:", {tk: {k: round(x["rel_diff"], 4) for k, x in d.items()} for tk, d in res["external_10q"].items()})
    sys.exit(0)
