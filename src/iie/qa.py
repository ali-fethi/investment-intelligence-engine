"""Quality assurance. Every check writes rows into qa_result; gaps go to data_gap.

Severity: pass | info | warn | fail.  `run_all` returns a summary dict.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from datetime import date

from .config import METRICS
from .db import companies, record_gap
from .derive import FiscalIndex, classify_diff, quarter_value
from .evidence import EvidenceStore
from .pit import load_facts
from .timeutil import et_to_utc_str, utcnow


class QA:
    def __init__(self, conn, run_id):
        self.conn, self.run_id = conn, run_id
        self.summary = defaultdict(lambda: defaultdict(int))

    def add(self, check, severity, detail, company_id=None, subject=None):
        self.summary[check][severity] += 1
        self.conn.execute("INSERT INTO qa_result(run_id, check_name, severity, company_id, subject, detail_json, "
                          "checked_at) VALUES (?,?,?,?,?,?,?)",
                          (self.run_id, check, severity, company_id, subject, json.dumps(detail, default=str),
                           utcnow()))


# ----------------------------------------------------------------------------------- provenance
def check_evidence_integrity(conn, qa: QA, store: EvidenceStore, full=True):
    rows = conn.execute("SELECT doc_id, url FROM source_document").fetchall()
    bad = 0
    for r in rows:
        try:
            store.read(r["doc_id"])
        except Exception as e:
            bad += 1
            qa.add("evidence_integrity", "fail", {"doc_id": r["doc_id"], "url": r["url"], "error": str(e)})
    qa.add("evidence_integrity", "pass" if not bad else "fail", {"documents": len(rows), "failures": bad})


def check_acceptance_crosscheck(conn, qa: QA):
    """Submissions-API acceptance (UTC) vs filing-header ACCEPTANCE-DATETIME (ET)."""
    rows = conn.execute("""SELECT d.accession_no, d.published_at, f.acceptance_datetime, f.company_id
                           FROM source_document d JOIN filing f ON f.accession_no=d.accession_no
                           WHERE d.published_at_basis='edgar_header_acceptance_datetime'
                           GROUP BY d.accession_no""").fetchall()
    mism = [r for r in rows if r["published_at"] != r["acceptance_datetime"]]
    for r in mism:
        qa.add("acceptance_crosscheck", "fail", dict(r), r["company_id"], r["accession_no"])
    qa.add("acceptance_crosscheck", "pass" if not mism else "fail",
           {"filings_compared": len(rows), "mismatches": len(mism)})


def check_observation_timing(conn, qa: QA):
    """A fact cannot be public before the period it describes has ended; flags unverified availability."""
    early = conn.execute("""SELECT obs_id, company_id, concept, period_end, available_at FROM observation
                            WHERE substr(available_at,1,10) < period_end""").fetchall()
    for r in early[:50]:
        qa.add("available_before_period_end", "fail", dict(r), r["company_id"], r["concept"])
    qa.add("available_before_period_end", "pass" if not early else "fail", {"violations": len(early)})
    unv = conn.execute("""SELECT company_id, availability_basis, count(*) n FROM observation
                          WHERE availability_verified=0 GROUP BY 1,2""").fetchall()
    for r in unv:
        qa.add("availability_unverified", "warn", dict(r), r["company_id"])
    total = conn.execute("SELECT count(*) FROM observation").fetchone()[0]
    qa.add("availability_unverified", "info", {"observations": total,
                                                "unverified": sum(r["n"] for r in unv)})


# ----------------------------------------------------------------------------------- duplicates & restatements
def check_duplicates(conn, qa: QA):
    # same fact identity reported with two different values in one filing => internal inconsistency
    rows = conn.execute("""SELECT company_id, natural_key, count(DISTINCT value) nv, group_concat(value) vals
                           FROM observation GROUP BY natural_key HAVING nv > 1""").fetchall()
    for r in rows[:100]:
        qa.add("conflicting_values_same_filing", "fail" if r["natural_key"].startswith("xbrl") else "warn",
               dict(r), r["company_id"], r["natural_key"])
    qa.add("conflicting_values_same_filing", "pass" if not rows else "warn", {"conflicts": len(rows)})
    # vendor history rewrites (same bar, different values across retrievals)
    rw = conn.execute("""SELECT company_id, natural_key, count(*) n FROM price_bar GROUP BY natural_key
                         HAVING n > 1""").fetchall()
    qa.add("vendor_history_rewrites", "info" if rw else "pass", {"bars_rewritten": len(rw),
                                                                "examples": [r["natural_key"] for r in rw[:5]]})


def check_restatements(conn, qa: QA):
    concepts = [qc.split(":") for m in METRICS.values() for qc in m["concepts"]]
    for c in companies(conn):
        n = 0
        examples = []
        for tax, concept in concepts:
            rows = conn.execute(
                """SELECT period_start, period_end, unit, count(DISTINCT value) nv, min(value) lo, max(value) hi,
                          count(DISTINCT accession_no) na
                   FROM observation WHERE company_id=? AND taxonomy=? AND concept=? AND source_kind='xbrl_fact'
                   GROUP BY period_start, period_end, unit HAVING nv > 1""",
                (c["company_id"], tax, concept)).fetchall()
            n += len(rows)
            examples += [dict(r, concept=concept) for r in rows[:2]]
        qa.add("restatements_detected", "info", {"periods_with_revised_values": n, "examples": examples[:6]},
               c["company_id"])


# ----------------------------------------------------------------------------------- completeness & definitions
def check_missing_quarters(conn, qa: QA, first_fy=2016):
    now = utcnow()
    for c in companies(conn):
        idx = FiscalIndex(load_facts(conn, c["company_id"], now, now))
        fys = [fy for (m, fy, fp) in idx.by_label if m == "revenue" and fp == "FY"]
        if not fys:
            continue
        last_fy = max(fys)
        for metric in ("revenue", "net_income", "operating_cash_flow", "eps_diluted"):
            # EPS is not additive: a missing Q4 is structural (10-K reports FY only), reported as info

            missing = []
            for fy in range(first_fy, last_fy + 2):
                for q in (1, 2, 3, 4):
                    item, _ = quarter_value(idx, metric, fy, q)
                    if item is None and _quarter_should_exist(idx, fy, q):
                        missing.append(f"FY{fy}Q{q}")
            structural = metric == "eps_diluted" and all(m_.endswith("Q4") for m_ in missing)
            sev = "pass" if not missing else ("info" if structural else "warn")
            qa.add("missing_quarters", sev, {"metric": metric, "missing": missing}, c["company_id"], metric)
            if missing:
                record_gap(conn, c["company_id"], f"quarterly:{metric}",
                           f"{metric} not reported or derivable for {', '.join(missing)}"
                           + (" (EPS is not additive, so Q4 cannot be derived from FY - 9M)"
                              if metric == "eps_diluted" else ""), qa.run_id)


def _quarter_should_exist(idx, fy, q):
    """Expected if revenue for that quarter exists (company was reporting then)."""
    return quarter_value(idx, "revenue", fy, q)[0] is not None


def check_definitions(conn, qa: QA):
    now = utcnow()
    for c in companies(conn):
        facts = load_facts(conn, c["company_id"], now, now)
        by_metric = defaultdict(set)
        conflicts = []
        for (m, ps, pe), f in facts.items():
            if f.source_kind == "xbrl_fact" and f.fiscal_period in ("FY", "Q1", "Q2", "Q3", "Q4", "INSTANT"):
                by_metric[m].add((f.fiscal_year, f.concept))
            for k in f.concept_conflicts:
                conflicts.append({"metric": m, "period_end": pe, "chosen": f.concept, "chosen_value": f.value, **k})
        for m, s in by_metric.items():
            concepts = sorted({cn for _, cn in s})
            if len(concepts) > 1:
                switch = {cn: sorted({fy for fy, c2 in s if c2 == cn}) for cn in concepts}
                qa.add("concept_switch_over_time", "info", {"metric": m, "fiscal_years_by_concept":
                                                            {k: [v[0], v[-1]] for k, v in switch.items()}},
                       c["company_id"], m)
        qa.add("definition_conflicts_same_period", "warn" if conflicts else "pass",
               {"count": len(conflicts), "examples": conflicts[:8]}, c["company_id"])


# ----------------------------------------------------------------------------------- reconciliations
def check_balance_sheet(conn, qa: QA):
    for c in companies(conn):
        rows = conn.execute(
            """SELECT a.accession_no, a.period_end, a.value assets, b.value le
               FROM observation a JOIN observation b ON a.accession_no=b.accession_no AND a.period_end=b.period_end
                 AND a.company_id=b.company_id
               WHERE a.company_id=? AND a.concept='Assets' AND b.concept='LiabilitiesAndStockholdersEquity'
                 AND a.unit='USD' AND b.unit='USD'""", (c["company_id"],)).fetchall()
        bad = [dict(r) for r in rows if abs(r["assets"] - r["le"]) >= 1]
        qa.add("balance_sheet_identity", "pass" if not bad else "fail",
               {"pairs_checked": len(rows), "failures": bad[:10]}, c["company_id"])


def check_quarter_additivity(conn, qa: QA):
    """Q1 + Q2 + Q3 (three-month XBRL values) = nine-month YTD, using the values known when the
    YTD figure was filed, so later restatements of a comparative are not mistaken for errors."""
    now = utcnow()
    for c in companies(conn):
        latest = FiscalIndex(load_facts(conn, c["company_id"], now, now))
        n, bad, rounding = 0, [], []
        for (m, fy, fp), f in list(latest.by_label.items()):
            if fp != "YTD9" or m not in ("revenue", "net_income", "operating_income"):
                continue
            at = FiscalIndex(load_facts(conn, c["company_id"], f.first_reported_at, now))
            ytd = at.get(m, fy, "YTD9")
            qs = [at.get(m, fy, f"Q{i}") for i in (1, 2, 3)]
            if ytd and all(qs) and all(x.source_kind == "xbrl_fact" for x in qs):
                n += 1
                diff = sum(x.value for x in qs) - ytd.value
                sev = classify_diff(diff, 4, ytd.value, *[x.value for x in qs])
                if sev == "fail":
                    bad.append({"metric": m, "fy": fy, "difference": diff, "as_of": f.first_reported_at})
                elif sev == "info":
                    rounding.append({"metric": m, "fy": fy, "difference": diff})
        qa.add("quarter_additivity", "pass" if not bad else "fail",
               {"fiscal_years_checked": n, "failures": bad, "within_rounding": rounding}, c["company_id"])


def check_releases_vs_xbrl(conn, qa: QA):
    """Each earnings-release value vs the first XBRL report of the same metric and quarter."""
    now = utcnow()
    for c in companies(conn):
        idx_all = load_facts(conn, c["company_id"], now, now)
        xbrl_only = {k: f for k, f in idx_all.items() if f.source_kind == "xbrl_fact"}
        fidx = FiscalIndex(xbrl_only)
        rel = conn.execute("SELECT * FROM observation WHERE company_id=? AND source_kind='earnings_release_table'",
                           (c["company_id"],)).fetchall()
        ok, bad, pending, rounding = 0, [], 0, []
        for r in rel:
            item, fact = quarter_value(fidx, r["concept"], r["fiscal_year"], int(r["fiscal_period"][1]))
            if item is None:
                pending += 1
                continue
            ref = (fact.first_reported_value if fact is not None else item["value"])
            diff = r["value"] - ref
            if r["unit"] == "USD/shares":
                sev = "pass" if abs(diff) < 0.005 else "fail"
            else:
                n_terms = 1 + (len(item["inputs"]) if item.get("kind") == "derived" else 1)
                sev = classify_diff(diff, n_terms, r["value"], ref)
            rec = {"obs_id": r["obs_id"], "metric": r["concept"], "period_end": r["period_end"],
                   "release_value": r["value"], "xbrl_value": ref, "difference": diff,
                   "xbrl_kind": item.get("kind"), "release_url": r["source_url"]}
            if sev == "pass":
                ok += 1
            elif sev == "info":
                rounding.append(rec)
            else:
                bad.append(rec)
        for b in bad:
            qa.add("earnings_release_vs_xbrl", "fail", b, c["company_id"], b["metric"])
        qa.add("earnings_release_vs_xbrl", "pass" if not bad else "fail",
               {"release_values": len(rel), "reconciled_exact": ok, "within_rounding": len(rounding),
                "rounding_cases": rounding, "mismatched": len(bad), "awaiting_xbrl": pending}, c["company_id"])


# ----------------------------------------------------------------------------------- market data
def check_prices(conn, qa: QA):
    for c in companies(conn):
        cid = c["company_id"]
        nonpos = conn.execute("SELECT count(*) FROM price_bar WHERE company_id=? AND (close<=0 OR low>high)",
                              (cid,)).fetchone()[0]
        dates = [r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM price_bar WHERE company_id=? "
                                            "ORDER BY trade_date", (cid,))]
        gaps = []
        for a, b in zip(dates, dates[1:]):
            if (date.fromisoformat(b) - date.fromisoformat(a)).days > 5:
                gaps.append([a, b])
        qa.add("price_sanity", "pass" if not nonpos and not gaps else "warn",
               {"bars": len(dates), "nonpositive_or_inverted": nonpos, "gaps_over_5_days": gaps[:10]}, cid)
        # split reconciliation: vendor splits vs XBRL stock-split ratio disclosures
        splits = conn.execute("SELECT DISTINCT ex_date, ratio FROM corporate_action WHERE company_id=? "
                              "AND action_type='split'", (cid,)).fetchall()
        xr = conn.execute("""SELECT DISTINCT value, accession_no FROM observation WHERE company_id=? AND
                             concept='StockholdersEquityNoteStockSplitConversionRatio1'""", (cid,)).fetchall()
        for s in splits:
            match = [x for x in xr if abs(x["value"] - s["ratio"]) < 1e-9]
            qa.add("split_vs_xbrl", "pass" if match else ("warn" if xr else "info"),
                   {"ex_date": s["ex_date"], "vendor_ratio": s["ratio"],
                    "xbrl_ratios": sorted({x["value"] for x in xr}),
                    "note": None if match else "no matching XBRL StockholdersEquityNoteStockSplitConversionRatio1"},
                   cid, s["ex_date"])


def check_fred_lag(conn, qa: QA, store: EvidenceStore):
    """Verify the 'next business day 17:00 ET' availability assumption against exact ALFRED vintages."""
    docs = conn.execute("SELECT doc_id, url FROM source_document WHERE doc_type='alfred_vintage_csv_check'").fetchall()
    for d in docs:
        sid = re.search(r"id=([^&]+)", d["url"]).group(1)
        vintage = re.search(r"vintage_date=([\d-]+)", d["url"]).group(1)
        rows = list(csv.reader(io.StringIO(store.read(d["doc_id"]).decode("utf-8-sig"))))[1:]
        vals = {r[0]: r[1] for r in rows if len(r) > 1 and r[1] not in ("", ".")}
        last_in_vintage = max(vals) if vals else None
        cutoff = et_to_utc_str(date.fromisoformat(vintage), 23, 59, 59)
        assumed = conn.execute("""SELECT max(obs_date) FROM macro_observation WHERE series_id=? AND available_at<=?
                                  AND value IS NOT NULL AND vintage_date IS NULL""",
                               (f"FRED:{sid}", cutoff)).fetchone()[0]
        mism = []
        for od, v in vals.items():
            r = conn.execute("""SELECT value FROM macro_observation WHERE series_id=? AND obs_date=? AND
                                vintage_date IS NULL ORDER BY mobs_id LIMIT 1""", (f"FRED:{sid}", od)).fetchone()
            if r and r["value"] is not None and abs(r["value"] - float(v)) > 1e-9:
                mism.append([od, float(v), r["value"]])
        aggressive = assumed is not None and last_in_vintage is not None and assumed > last_in_vintage
        qa.add("fred_availability_lag", "fail" if aggressive else "pass",
               {"series": sid, "vintage": vintage, "last_obs_in_vintage": last_in_vintage,
                "last_obs_assumed_available": assumed, "values_revised_since_vintage": len(mism),
                "revision_examples": mism[:5]}, None, f"{sid}@{vintage}")


def check_snapshots(conn, qa: QA):
    from .snapshot import lookahead_audit, snapshot_path
    for s in conn.execute("SELECT snapshot_id FROM snapshot").fetchall():
        p = snapshot_path(conn, s["snapshot_id"])
        if not p.exists():
            qa.add("snapshot_lookahead", "fail", {"snapshot_id": s["snapshot_id"], "error": "file missing"})
            continue
        audit = lookahead_audit(json.loads(p.read_text())["content"])
        qa.add("snapshot_lookahead", audit["status"], {"snapshot_id": s["snapshot_id"], **audit})


def run_all(conn, store: EvidenceStore, run_id) -> dict:
    qa = QA(conn, run_id)
    for fn in (lambda: check_evidence_integrity(conn, qa, store), lambda: check_acceptance_crosscheck(conn, qa),
               lambda: check_observation_timing(conn, qa), lambda: check_duplicates(conn, qa),
               lambda: check_restatements(conn, qa), lambda: check_missing_quarters(conn, qa),
               lambda: check_definitions(conn, qa), lambda: check_balance_sheet(conn, qa),
               lambda: check_quarter_additivity(conn, qa), lambda: check_releases_vs_xbrl(conn, qa),
               lambda: check_prices(conn, qa), lambda: check_fred_lag(conn, qa, store),
               lambda: check_snapshots(conn, qa)):
        fn()
        conn.commit()
    return {k: dict(v) for k, v in qa.summary.items()}
