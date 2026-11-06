"""Point-in-time snapshot: build, persist immutably, verify, render.

A snapshot is identified by (cutoff, knowledge_time).  Its content hash covers everything except
creation metadata, so rebuilding the same pair must reproduce the same hash bit-for-bit.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from . import __version__
from .config import ALFRED_REVISED_SERIES, FRED_DAILY_SERIES, METRICS, SNAPSHOT_DIR
from .derive import (FiscalIndex, as_traded_close, classify_diff, derived, latest_quarter, quarter_value,
                     split_factor_between, ttm)
from .pit import bar_on_or_before, corporate_actions, latest_bar, load_facts, macro_value
from .timeutil import ET, parse_utc, utcnow

FLOW_METRICS = ["revenue", "cost_of_revenue", "research_and_development", "operating_income", "net_income",
                "eps_diluted", "diluted_shares", "operating_cash_flow", "capex"]
INSTANT_METRICS = ["total_assets", "total_liabilities", "stockholders_equity", "liabilities_and_equity",
                   "cash_and_equivalents"]
UST_TENORS = ["1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "5 Yr", "10 Yr", "30 Yr"]

RULES = [
    "Visibility: an item is included only if available_at <= cutoff and retrieved_at <= knowledge_time.",
    "SEC XBRL facts become available at the EDGAR acceptance timestamp of the filing that carries them.",
    "Earnings-release figures (8-K EX-99.1) are used only for periods with no XBRL fact visible at the cutoff.",
    "Restatements: the latest value available at the cutoff is used; the first reported value is kept in the audit trail.",
    "Prices: the vendor's split-adjusted close is converted back to the as-traded close; adjustments for splits "
    "after the cutoff are never applied to returns.",
    "Derived values are only as available as their latest input; inputs are listed by row id.",
    "Missing data is reported as a gap, never estimated.",
]


def _item_from_bar(bar, splits, label):
    px, factor, method = as_traded_close(bar, splits)
    return {"value": round(px, 6), "unit": "USD/share", "trade_date": bar["trade_date"], "vendor_close": bar["close"],
            "vendor_adjustment_basis": bar["adjustment_basis"], "unadjustment_factor": factor, "methodology": method,
            "volume_vendor": bar["volume"], "provider": bar["provider"], "available_at": bar["available_at"],
            "availability_basis": bar["availability_basis"], "retrieved_at": bar["retrieved_at"],
            "source_doc_id": bar["source_doc_id"], "evidence": {"table": "price_bar", "id": bar["bar_id"]},
            "reliability": "secondary_vendor_unlicensed", "label": label}


def build_company(conn, company, cutoff, kt) -> dict:
    cid = company["company_id"]
    facts = load_facts(conn, cid, cutoff, kt)
    idx = FiscalIndex(facts)
    out = {"company": {"ticker": company["ticker"], "cik": company["cik"], "name": company["name"],
                       "fiscal_year_end_mmdd": company["fiscal_year_end"]}, "gaps": [], "checks": []}
    lq = latest_quarter(idx, "revenue")
    if lq is None:
        out["gaps"].append("No quarterly revenue visible at cutoff.")
        return out
    fy, q = lq
    out["latest_fiscal_quarter"] = f"FY{fy} Q{q}"
    flows = {}
    for m in FLOW_METRICS:
        cur, _ = quarter_value(idx, m, fy, q)
        pyq, _ = quarter_value(idx, m, fy - 1, q)
        fy_items = [k for k in idx.by_label if k[0] == m and k[2] == "FY"]
        last_fy = max((k[1] for k in fy_items), default=None)
        entry = {"latest_quarter": cur, "same_quarter_prior_year": pyq,
                 "latest_fiscal_year": idx.get(m, last_fy, "FY").as_item() if last_fy else None,
                 "ttm": ttm(idx, m, fy, q)}
        for k, v in entry.items():
            if v is None and not (k == "ttm" and not METRICS[m]["additive"]):
                out["gaps"].append(f"{m}.{k} not available at cutoff (not reported and not derivable)")
        flows[m] = entry
    out["income_and_cash_flow"] = flows

    # derived operating metrics for the latest quarter
    d = {}
    rev, cor = flows["revenue"]["latest_quarter"], flows["cost_of_revenue"]["latest_quarter"]
    if rev and cor:
        d["gross_profit_quarter"] = derived("gross_profit", rev["value"] - cor["value"], "USD",
                                            "gross profit = revenue - cost of revenue",
                                            [_inref(rev, "revenue"), _inref(cor, "cost_of_revenue")], fy, f"Q{q}")
    oi = flows["operating_income"]["latest_quarter"]
    if rev and oi and rev["value"]:
        d["operating_margin_quarter"] = derived("operating_margin", oi["value"] / rev["value"], "ratio",
                                                "operating margin = operating income / revenue",
                                                [_inref(oi, "operating_income"), _inref(rev, "revenue")], fy, f"Q{q}")
    for span in ("latest_quarter", "ttm"):
        cfo, capex = flows["operating_cash_flow"][span], flows["capex"][span]
        if cfo and capex:
            d[f"free_cash_flow_{span}"] = derived(
                "free_cash_flow", cfo["value"] - capex["value"], "USD",
                "FCF = operating cash flow - purchases of property and equipment (company-agnostic definition; "
                "differs from issuers' own FCF definitions where they include finance-lease principal)",
                [_inref(cfo, "operating_cash_flow"), _inref(capex, "capex")], fy, span)
    out["derived"] = d

    # balance sheet at latest instant
    inst = idx.instants.get("total_assets", {})
    if inst:
        bs_date = max(inst)
        bs = {"as_of": bs_date}
        for m in INSTANT_METRICS:
            f = idx.instants.get(m, {}).get(bs_date)
            bs[m] = f.as_item() if f else None
            if f is None:
                out["gaps"].append(f"balance sheet {m} at {bs_date} not reported in XBRL (standard taxonomy)")
        out["balance_sheet"] = bs
        out["checks"] += _balance_checks(bs)
    out["checks"] += _quarter_sum_checks(idx, fy)
    conflicts = [(m, k) for (m, *_), f in facts.items() for k in f.concept_conflicts]
    if conflicts:
        out["checks"].append({"check": "accounting_definition_conflicts", "severity": "warn",
                              "count": len(conflicts), "examples": [{"metric": m, **k} for m, k in conflicts[:5]]})

    out["market"] = _market(conn, company, cutoff, kt, idx, out["gaps"])
    out["events"] = _events(conn, cid, cutoff, kt)
    out["documents"] = _documents(conn, cid, cutoff, kt)
    out["gaps"].append("Forward-looking earnings dates: no source with verifiable first-announcement timestamp; "
                       "not included.")
    return out


def _inref(item, role):
    if item.get("kind") == "derived":
        return {"role": role, "table": "derived(inline)", "methodology": item["methodology"],
                "inputs": item["inputs"], "value": item["value"], "available_at": item["available_at"]}
    return {"role": role, "table": "observation", "id": item["evidence"]["id"], "value": item["value"],
            "concept": item["concept"], "available_at": item["available_at"]}


def _balance_checks(bs):
    res = []
    a, le = bs.get("total_assets"), bs.get("liabilities_and_equity")
    if a and le:
        diff = a["value"] - le["value"]
        res.append({"check": "assets_equal_liabilities_and_equity", "severity": "pass" if abs(diff) < 1 else "fail",
                    "assets": a["value"], "liabilities_and_equity": le["value"], "difference": diff})
    l, e = bs.get("total_liabilities"), bs.get("stockholders_equity")
    if a and l and e:
        diff = a["value"] - (l["value"] + e["value"])
        res.append({"check": "assets_equal_liabilities_plus_equity", "severity": "pass" if abs(diff) < 1 else "warn",
                    "difference": diff, "note": "non-zero difference can be noncontrolling interest or "
                                                "temporary equity, which are outside these two concepts"})
    return res


def _quarter_sum_checks(idx, fy):
    res = []
    for m in ("revenue", "net_income"):
        q = [idx.get(m, fy, f"Q{i}") for i in (1, 2, 3)]
        ytd = idx.get(m, fy, "YTD9")
        if all(q) and ytd and all(x.source_kind == "xbrl_fact" for x in q):
            diff = sum(x.value for x in q) - ytd.value
            sev = classify_diff(diff, 4, ytd.value, *[x.value for x in q])
            res.append({"check": f"{m}_Q1+Q2+Q3_equals_9M_YTD_FY{fy}", "severity": sev, "difference": diff,
                        "note": "info = difference within rounding of figures reported in millions"
                        if sev == "info" else None})
    return res


def _market(conn, company, cutoff, kt, idx, gaps):
    cid = company["company_id"]
    bar = latest_bar(conn, cid, cutoff, kt)
    if bar is None:
        gaps.append("No price bar visible at cutoff.")
        return {}
    splits = corporate_actions(conn, cid, kt, "split")
    m = {"last_close": _item_from_bar(bar, splits, "last close at or before cutoff")}
    then = (date.fromisoformat(bar["trade_date"]) - timedelta(days=365)).isoformat()
    b0 = bar_on_or_before(conn, cid, then, kt)
    if b0 is not None:
        p1, p0 = m["last_close"]["value"], as_traded_close(b0, splits)[0]
        f = split_factor_between(splits, b0["trade_date"], bar["trade_date"])
        m["price_return_1y"] = {"value": p1 / (p0 / f) - 1, "unit": "ratio", "kind": "derived",
                                "methodology": "price return = close_t / (close_t-365d as traded / split factor "
                                               "between the two dates, splits known by t) - 1; excludes dividends",
                                "inputs": [{"table": "price_bar", "id": bar["bar_id"]},
                                           {"table": "price_bar", "id": b0["bar_id"]}],
                                "available_at": bar["available_at"]}
    known = [s for s in corporate_actions(conn, cid, kt) if s["available_at"] <= cutoff]
    retrieved = bar["retrieved_at"][:10]
    m["corporate_actions_known"] = [
        {"type": s["action_type"], "ex_date": s["ex_date"], "ratio": s["ratio"],
         "amount_as_traded": (s["amount"] * split_factor_between(splits, s["ex_date"], retrieved)
                              if s["amount"] is not None else None),
         "available_at": s["available_at"], "availability_basis": s["availability_basis"],
         "evidence": {"table": "corporate_action", "id": s["action_id"]}} for s in known[-8:]]
    shares = idx.instants.get("shares_outstanding_cover", {})
    if shares:
        sd = max(shares)
        sf = shares[sd]
        post = split_factor_between(splits, sd, bar["trade_date"])
        m["market_cap"] = derived("market_cap", m["last_close"]["value"] * sf.value * post, "USD",
                                  "market cap = as-traded close x cover-page shares outstanding (x split factor for "
                                  "splits between share-count date and price date)",
                                  [{"role": "price", "table": "price_bar", "id": bar["bar_id"],
                                    "available_at": bar["available_at"]},
                                   {"role": "shares", "table": "observation", "id": sf.obs_id, "value": sf.value,
                                    "concept": sf.concept, "available_at": sf.available_at}])
        m["market_cap"]["shares_as_of"] = sd
    else:
        gaps.append("Market cap not computed: no undimensioned dei:EntityCommonStockSharesOutstanding visible "
                    "(multi-class issuers report shares per class, which SEC companyfacts omits).")
    return m


def _events(conn, cid, cutoff, kt):
    rows = conn.execute(
        """SELECT e.* FROM event e JOIN source_document d ON d.doc_id=e.source_doc_id
           WHERE e.company_id=? AND e.available_at<=? AND d.retrieved_at<=? ORDER BY e.available_at DESC""",
        (cid, cutoff, kt)).fetchall()

    def ev(r):
        return {"type": r["event_type"], "event_date": r["event_date"], "available_at": r["available_at"],
                "availability_basis": r["availability_basis"], "accession_no": r["accession_no"],
                "details": json.loads(r["details_json"]), "evidence": {"table": "event", "id": r["event_id"]}}
    last = {}
    for r in rows:
        last.setdefault(r["event_type"], ev(r))
    return {"last_by_type": last, "recent": [ev(r) for r in rows[:10]],
            "next_scheduled": None, "next_scheduled_note": "not available from a verifiable source (see gaps)"}


def _documents(conn, cid, cutoff, kt):
    out = []
    for typ in ("10-K", "10-Q", "EX-99.1"):
        r = conn.execute(
            """SELECT d.*, f.form, f.report_date, f.acceptance_datetime FROM source_document d
               JOIN filing f ON f.accession_no=d.accession_no
               WHERE d.company_id=? AND d.doc_type=? AND f.acceptance_datetime<=? AND d.retrieved_at<=?
               ORDER BY f.acceptance_datetime DESC LIMIT 1""", (cid, typ, cutoff, kt)).fetchone()
        if r:
            out.append({"doc_type": typ, "title": r["title"], "url": r["url"], "accession_no": r["accession_no"],
                        "report_date": r["report_date"], "available_at": r["acceptance_datetime"],
                        "sha256": r["sha256"], "retrieved_at": r["retrieved_at"],
                        "evidence": {"table": "source_document", "id": r["doc_id"]}})
    return out


def build_macro(conn, cutoff, kt, gaps):
    out = {"treasury_par_curve": {}, "fred": {}}
    for t in UST_TENORS:
        r = macro_value(conn, f"UST:{t}", cutoff, kt)
        if r:
            out["treasury_par_curve"][t] = _macro_item(r)
    if not out["treasury_par_curve"]:
        gaps.append("No Treasury curve visible at cutoff.")
    for sid in list(FRED_DAILY_SERIES) + list(ALFRED_REVISED_SERIES):
        r = macro_value(conn, f"FRED:{sid}", cutoff, kt)
        if r:
            out["fred"][sid] = _macro_item(r)
        else:
            gaps.append(f"FRED:{sid} not visible at cutoff (no vintage ingested at or before cutoff).")
    return out


def _macro_item(r):
    return {"value": r["value"], "unit": r["unit"], "obs_date": r["obs_date"], "definition": r["definition"],
            "vintage_date": r["vintage_date"], "available_at": r["available_at"],
            "availability_basis": r["availability_basis"], "availability_verified": bool(r["availability_verified"]),
            "source_url": r["source_url"], "retrieved_at": r["retrieved_at"],
            "evidence": {"table": "macro_observation", "id": r["mobs_id"]}}


def build_snapshot(conn, cutoff: str, knowledge_time: str, tickers=None) -> dict:
    from .db import companies
    content = {"meta": {"cutoff_utc": cutoff, "knowledge_time_utc": knowledge_time, "code_version": __version__,
                        "rules": RULES},
               "companies": {}, "macro": {}, "gaps": []}
    for c in companies(conn, tickers):
        content["companies"][c["ticker"]] = build_company(conn, c, cutoff, knowledge_time)
    content["macro"] = build_macro(conn, cutoff, knowledge_time, content["gaps"])
    gaps = conn.execute("SELECT g.*, c.ticker FROM data_gap g LEFT JOIN company c USING(company_id) "
                        "WHERE g.detected_at<=? ORDER BY gap_id", (knowledge_time,)).fetchall()
    content["gaps"] += [f"[{g['ticker'] or 'ALL'}:{g['scope']}] {g['description']}" for g in gaps]
    content["lookahead_audit"] = lookahead_audit(content)
    return content


def canonical(content: dict) -> bytes:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()


def content_hash(content: dict) -> str:
    return hashlib.sha256(canonical(content)).hexdigest()


def walk(o, path=""):
    if isinstance(o, dict):
        yield path, o
        for k, v in o.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")


def lookahead_audit(content) -> dict:
    cutoff, kt = content["meta"]["cutoff_utc"], content["meta"]["knowledge_time_utc"]
    violations, checked = [], 0
    for path, d in walk({k: v for k, v in content.items() if k != "meta"}):
        if "available_at" in d and d["available_at"]:
            checked += 1
            if d["available_at"] > cutoff:
                violations.append({"path": path, "available_at": d["available_at"]})
        if "retrieved_at" in d and d["retrieved_at"] and d["retrieved_at"] > kt:
            violations.append({"path": path, "retrieved_at": d["retrieved_at"]})
    return {"items_checked": checked, "violations": violations, "status": "pass" if not violations else "fail"}


def referenced_rows(content):
    refs = set()
    for _, d in walk(content):
        if "table" in d and "id" in d and d["table"] in ("observation", "price_bar", "macro_observation", "event",
                                                         "corporate_action", "source_document"):
            refs.add((d["table"], d["id"]))
        if "source_doc_id" in d and d["source_doc_id"]:
            refs.add(("source_document", d["source_doc_id"]))
    return refs


def create_snapshot(conn, cutoff: str, knowledge_time: str | None = None, tickers=None, out_dir: Path = SNAPSHOT_DIR):
    kt = knowledge_time or utcnow()
    content = build_snapshot(conn, cutoff, kt, tickers)
    h = content_hash(content)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    refs = referenced_rows(content)
    sid = conn.execute("SELECT coalesce(max(snapshot_id), 0) + 1 FROM snapshot").fetchone()[0]
    path = out_dir / f"snapshot_{sid:04d}_{parse_utc(cutoff).astimezone(ET):%Y-%m-%dT%H%M}ET.json"
    conn.execute("INSERT INTO snapshot(snapshot_id, cutoff, knowledge_time, created_at, code_version, content_sha256, "
                 "path, item_count) VALUES (?,?,?,?,?,?,?,?)",
                 (sid, cutoff, kt, utcnow(), __version__, h, path.name, len(refs)))
    conn.executemany("INSERT INTO snapshot_item VALUES (?,?,?)", [(sid, t, i) for t, i in sorted(refs)])
    for tk, comp in content["companies"].items():
        cid = conn.execute("SELECT company_id FROM company WHERE ticker=?", (tk,)).fetchone()[0]
        for name, dm in (comp.get("derived") or {}).items():
            _persist_derived(conn, sid, cid, dm, cutoff)
        for m, e in (comp.get("income_and_cash_flow") or {}).items():
            for span in ("latest_quarter", "same_quarter_prior_year", "ttm"):
                it = e.get(span)
                if it and it.get("kind") == "derived":
                    _persist_derived(conn, sid, cid, it, cutoff)
        mc = (comp.get("market") or {}).get("market_cap")
        if mc:
            _persist_derived(conn, sid, cid, mc, cutoff)
    doc = {"snapshot_id": sid, "created_at": utcnow(), "content_sha256": h, "content": content}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str))
    (path.with_suffix(".md")).write_text(render_markdown(content, sid, h))
    conn.commit()
    return sid, path, h, content


def snapshot_path(conn, sid, out_dir: Path = SNAPSHOT_DIR) -> Path:
    r = conn.execute("SELECT path FROM snapshot WHERE snapshot_id=?", (sid,)).fetchone()
    return Path(out_dir) / r["path"]


def _persist_derived(conn, sid, cid, dm, cutoff):
    conn.execute("""INSERT INTO derived_metric(snapshot_id, company_id, metric_id, value, unit, period_start, period_end,
                    fiscal_year, fiscal_period, methodology, inputs_json, available_at, as_of_cutoff, computed_at,
                    code_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (sid, cid, dm["metric"], dm["value"], dm["unit"], dm.get("period_start"), dm.get("period_end"),
                  dm.get("fiscal_year"), dm.get("fiscal_period"), dm["methodology"],
                  json.dumps(dm["inputs"], sort_keys=True, default=str), dm["available_at"], cutoff, utcnow(),
                  __version__))


def verify_snapshot(conn, sid: int, store=None, out_dir: Path = SNAPSHOT_DIR) -> dict:
    """Rebuild from the database with the stored clocks and compare hashes; re-check evidence bytes."""
    row = conn.execute("SELECT * FROM snapshot WHERE snapshot_id=?", (sid,)).fetchone()
    stored = json.loads(snapshot_path(conn, sid, out_dir).read_text())
    file_hash_ok = content_hash(stored["content"]) == row["content_sha256"]
    tickers = list(stored["content"]["companies"])
    rebuilt = build_snapshot(conn, row["cutoff"], row["knowledge_time"], tickers)
    # the code version is part of the content; compare as if built by the version that wrote the snapshot, so
    # a mismatch means the current code computes something different (a behaviour change), not a version bump
    rebuilt["meta"]["code_version"] = row["code_version"]
    rebuild_ok = content_hash(rebuilt) == row["content_sha256"]
    evidence_ok, bad = True, []
    if store is not None:
        for (t, i) in referenced_rows(stored["content"]):
            if t == "source_document":
                try:
                    store.read(i)
                except Exception as e:  # corrupted or missing evidence
                    evidence_ok = False
                    bad.append({"doc_id": i, "error": str(e)})
    return {"snapshot_id": sid, "stored_hash": row["content_sha256"], "file_hash_matches": file_hash_ok,
            "code_version_matches": row["code_version"] == __version__,
            "rebuild_hash_matches": rebuild_ok, "evidence_bytes_verified": evidence_ok, "evidence_errors": bad,
            "lookahead": stored["content"]["lookahead_audit"]["status"]}


# ---------------------------------------------------------------------------------------------- render
def _fmt(v, unit):
    if v is None:
        return "—"
    if unit == "USD":
        return f"${v / 1e6:,.0f}m"
    if unit == "ratio":
        return f"{v * 100:.1f}%"
    if unit in ("USD/shares", "USD/share"):
        return f"${v:,.2f}"
    if unit == "shares":
        return f"{v / 1e6:,.0f}m sh"
    return f"{v:,.4g} {unit}"


def _src(it):
    if not it:
        return ""
    if it.get("kind") == "derived":
        return f"derived: {it['methodology']} [inputs: " + ", ".join(
            f"{i.get('table')}#{i.get('id', '')}" for i in it["inputs"][:6]) + "]"
    acc = it.get("accession_no") or ""
    return f"{it.get('concept')} · {it.get('filing_form') or ''} {acc} · avail {it['available_at']} · obs#{it['evidence']['id']}"


def render_markdown(c, sid, h) -> str:
    L = [f"# Point-in-time snapshot #{sid}", "",
         f"- Cutoff (market time): `{c['meta']['cutoff_utc']}`",
         f"- Knowledge time (system time): `{c['meta']['knowledge_time_utc']}`",
         f"- Content SHA-256: `{h}`",
         f"- Look-ahead audit: **{c['lookahead_audit']['status']}** "
         f"({c['lookahead_audit']['items_checked']} timestamped items checked)", ""]
    for tk, comp in c["companies"].items():
        L += [f"## {tk} — {comp['company']['name']} (CIK {comp['company']['cik']})", ""]
        if "latest_fiscal_quarter" not in comp:
            L += ["No data.", ""]
            continue
        L += [f"Latest fiscal quarter visible: **{comp['latest_fiscal_quarter']}**", "",
              "| Metric | Latest quarter | Same qtr prior yr | TTM | Source of latest quarter |",
              "|---|---:|---:|---:|---|"]
        for m, e in comp["income_and_cash_flow"].items():
            lq, py, tt = e["latest_quarter"], e["same_quarter_prior_year"], e["ttm"]
            u = METRICS[m]["unit"]
            L.append(f"| {m} | {_fmt(lq and lq['value'], u)} | {_fmt(py and py['value'], u)} | "
                     f"{_fmt(tt and tt['value'], u)} | {_src(lq)} |")
        L += ["", "Derived:", ""]
        for k, v in comp.get("derived", {}).items():
            L.append(f"- {k}: {_fmt(v['value'], v['unit'])} — {v['methodology']}")
        bs = comp.get("balance_sheet")
        if bs:
            L += ["", f"Balance sheet as of {bs['as_of']}:", ""]
            for m in INSTANT_METRICS:
                it = bs.get(m)
                L.append(f"- {m}: {_fmt(it and it['value'], 'USD')} {('· ' + _src(it)) if it else ''}")
        mk = comp.get("market", {})
        if mk.get("last_close"):
            lc = mk["last_close"]
            L += ["", f"Market: as-traded close {_fmt(lc['value'], 'USD/share')} on {lc['trade_date']} "
                      f"(vendor close {lc['vendor_close']:.4f} × {lc['unadjustment_factor']:g}); "
                      f"1y price return {_fmt(mk.get('price_return_1y', {}).get('value'), 'ratio')}; "
                      f"market cap {_fmt(mk.get('market_cap', {}).get('value'), 'USD')}"]
        ev = comp.get("events", {}).get("last_by_type", {})
        if ev:
            L += ["", "Last events:", ""]
            for t, e in ev.items():
                L.append(f"- {t}: {e['event_date']} (public {e['available_at']}, {e['accession_no'] or ''})")
        if comp.get("documents"):
            L += ["", "Latest source documents:", ""]
            for d in comp["documents"]:
                L.append(f"- {d['doc_type']} — {d['url']} (sha256 {d['sha256'][:12]}…, public {d['available_at']})")
        if comp.get("checks"):
            L += ["", "Checks at cutoff:", ""]
            for ch in comp["checks"]:
                L.append(f"- {ch['check']}: **{ch['severity']}**")
        if comp.get("gaps"):
            L += ["", "Gaps:", ""] + [f"- {g}" for g in comp["gaps"]]
        L.append("")
    L += ["## Rates and macro", "", "| Series | Value | Obs date | Available at | Basis |", "|---|---:|---|---|---|"]
    for t, it in c["macro"].get("treasury_par_curve", {}).items():
        L.append(f"| UST {t} | {it['value']} | {it['obs_date']} | {it['available_at']} | {it['availability_basis']} |")
    for s, it in c["macro"].get("fred", {}).items():
        L.append(f"| FRED {s} | {it['value']} | {it['obs_date']} | {it['available_at']} | {it['availability_basis']} |")
    L += ["", "## Global gaps", ""] + [f"- {g}" for g in c["gaps"]] + [""]
    return "\n".join(L)
