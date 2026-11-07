"""Command-line entry point:  python -m iie <command>  (or the `iie` console script)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .config import DB_PATH
from .db import companies, connect, init_db, ingest_run
from .evidence import EvidenceStore, Transport
from .timeutil import parse_cutoff


REPLAY = None


def _store(conn, online=True):
    if REPLAY is not None:
        return EvidenceStore(conn, transport=REPLAY)
    return EvidenceStore(conn, transport=Transport() if online else None)


def cmd_init(a):
    conn = connect(a.db)
    init_db(conn)
    print(f"initialised {a.db}")


def cmd_ingest(a):
    from .ingest import crosscheck, documents, earnings_release, events, macro, macro_events, prices, sec
    conn = connect(a.db)
    init_db(conn)
    store = _store(conn)
    targets = a.what
    if "all" in targets:
        targets = ["sec", "docs", "releases", "prices", "events", "macro", "context", "benchmarks",
                   "macro_events", "crosscheck", "expectations"]
    params = {"targets": targets, "tickers": a.tickers, "since": a.since, "docs_since": a.docs_since,
              "vintages": a.vintage, "vintage_start": a.vintage_start}
    with ingest_run(conn, "ingest:" + ",".join(targets), params) as (run_id, stats):
        for c in companies(conn, a.tickers):
            if "sec" in targets:
                print(f"[{c['ticker']}] SEC submissions + companyfacts")
                sec.ingest_filings(conn, store, c, run_id, stats)
                conn.commit()
                sec.ingest_companyfacts(conn, store, c, run_id, stats)
                conn.commit()
            c = conn.execute("SELECT * FROM company WHERE company_id=?", (c["company_id"],)).fetchone()
            if "docs" in targets:
                print(f"[{c['ticker']}] filing documents since {a.docs_since}")
                documents.ingest_filing_documents(conn, store, c, a.docs_since, run_id, stats)
            if "releases" in targets:
                earnings_release.ingest_releases(conn, c, run_id, stats, store)
                conn.commit()
            if "prices" in targets:
                print(f"[{c['ticker']}] prices since {a.since}")
                prices.ingest_prices(conn, store, c, a.since, run_id, stats)
                conn.commit()
            if "events" in targets:
                events.build_events(conn, c, a.since, run_id, stats)
                conn.commit()
        if "context" in targets:
            for c in companies(conn, None, role="context"):
                print(f"[{c['ticker']}] context issuer: EDGAR filing index")
                sec.ingest_filings(conn, store, c, run_id, stats)
                c = conn.execute("SELECT * FROM company WHERE company_id=?", (c["company_id"],)).fetchone()
                events.build_events(conn, c, a.since, run_id, stats)
                conn.commit()
        if "benchmarks" in targets:
            for c in companies(conn, None, role="benchmark"):
                print(f"[{c['ticker']}] benchmark prices since {a.since}")
                prices.ingest_prices(conn, store, c, a.since, run_id, stats)
                conn.commit()
        if "macro_events" in targets:
            print("FOMC statements and macro data-release dates")
            macro_events.ingest_fomc(conn, store, range(int(a.since[:4]), date.today().year + 1), run_id, stats)
            macro_events.ingest_macro_releases(conn, store, a.since, run_id, stats)
            conn.commit()
        if "crosscheck" in targets:
            for c in companies(conn, None) + [b for b in companies(conn, None, role="benchmark") if b["kind"] == "etf"]:
                print(f"[{c['ticker']}] Nasdaq.com cross-check prices")
                crosscheck.ingest_nasdaq(conn, store, c, a.since, run_id, stats)
                conn.commit()
            crosscheck.ingest_fred_indices(conn, store, a.since, run_id, stats)
            conn.commit()
        if "expectations" in targets:
            from .expectations import ingest_registry
            print("expectations registry (fetch sources, verify quotes, grade provenance)")
            ingest_registry(conn, store, run_id, stats)
            conn.commit()
        if "macro" in targets:
            y0 = int(a.since[:4])
            print("Treasury par yield curve", y0, "->", date.today().year)
            macro.ingest_treasury(conn, store, range(y0, date.today().year + 1), run_id, stats)
            conn.commit()
            print("FRED daily series")
            macro.ingest_fred_daily(conn, store, a.since, run_id, stats)
            conn.commit()
            vint = macro.month_end_vintages(a.vintage_start) + list(a.vintage or [])
            print(f"ALFRED vintages ({len(vint)} dates per revised series)")
            macro.ingest_alfred(conn, store, vint, a.since, run_id, stats)
            if a.vintage:
                macro.ingest_alfred_checks(conn, store, a.vintage, a.since, run_id, stats)
            conn.commit()
    print(json.dumps({k: v for k, v in stats.items() if not isinstance(v, list)}, indent=1, sort_keys=True))
    for k, v in stats.items():
        if isinstance(v, list):
            print(f"{k}: {len(v)} (first: {v[:2]})")


def cmd_rebuild(a):
    """Rebuild a fresh database from the evidence store of an existing one (no network access)."""
    global REPLAY
    from pathlib import Path
    from .evidence import ReplayTransport
    src = Path(a.from_db)
    if Path(a.db).resolve() == src.resolve():
        sys.exit("--from-db must differ from --db (copy the old database aside first)")
    REPLAY = ReplayTransport(src)
    old = REPLAY.old
    for r in old.execute("SELECT component, params_json FROM ingest_run WHERE component LIKE 'ingest:%' "
                         "AND status='ok' ORDER BY run_id"):
        p = json.loads(r["params_json"])
        print(f"replaying {r['component']} {p}")
        ns = argparse.Namespace(db=a.db, what=p["targets"], tickers=p.get("tickers"), since=p["since"],
                                docs_since=p["docs_since"], vintage=p.get("vintages"),
                                vintage_start=p.get("vintage_start", "2019-01-31"))
        cmd_ingest(ns)
    REPLAY = None


def cmd_qa(a):
    from .qa import run_all
    conn = connect(a.db)
    with ingest_run(conn, "qa", {}) as (run_id, stats):
        summary = run_all(conn, _store(conn, online=False), run_id)
        stats.update({"checks": len(summary)})
    print(json.dumps(summary, indent=1, sort_keys=True))
    print(f"details: SELECT * FROM qa_result WHERE run_id={run_id}")


def cmd_snapshot(a):
    from .snapshot import create_snapshot
    conn = connect(a.db)
    cutoff = parse_cutoff(a.cutoff)
    kt = parse_cutoff(a.knowledge_time) if a.knowledge_time else None
    sid, path, h, content = create_snapshot(conn, cutoff, kt, a.tickers)
    print(f"snapshot #{sid} cutoff={cutoff} sha256={h}\n  {path}\n  {path.with_suffix('.md')}")
    print(f"look-ahead audit: {content['lookahead_audit']['status']} "
          f"({content['lookahead_audit']['items_checked']} items)")


def cmd_verify(a):
    from .snapshot import verify_snapshot
    conn = connect(a.db)
    res = verify_snapshot(conn, a.snapshot, _store(conn, online=False))
    print(json.dumps(res, indent=1))
    ok = res["file_hash_matches"] and res["rebuild_hash_matches"] and res["evidence_bytes_verified"] \
        and res["lookahead"] == "pass"
    sys.exit(0 if ok else 1)


def cmd_search(a):
    from .search import format_citation, search
    conn = connect(a.db)
    hits = search(conn, a.query, a.ticker, parse_cutoff(a.as_of) if a.as_of else None, a.limit)
    for i, h in enumerate(hits, 1):
        print(f"{i}. {h['snippet']}\n   ↳ {format_citation(h)}\n")
    if not hits:
        print("no results")


def cmd_case(a):
    from pathlib import Path
    from .casestudy import run_case
    from .timeutil import utcnow
    conn = connect(a.db)
    init_db(conn)
    kt = parse_cutoff(a.knowledge_time) if a.knowledge_time else utcnow()
    for acc in a.accession:
        r = conn.execute("SELECT event_id FROM event WHERE accession_no=? AND event_type='earnings_release'",
                         (acc,)).fetchone()
        if r is None:
            print(f"{acc}: no earnings_release event")
            continue
        res, path, val = run_case(conn, r[0], kt, Path(a.out))
        bad = [v for v in val if v[1] == "fail"]
        print(f"{acc} -> {path}  (validation: {len(val) - len(bad)} ok/n.a., {len(bad)} fail)")


def cmd_reactions(a):
    from pathlib import Path
    from .casestudy import render_summary, summarise
    from .timeutil import utcnow
    conn = connect(a.db)
    kt = parse_cutoff(a.knowledge_time) if a.knowledge_time else utcnow()
    rows = summarise(conn, a.tickers or ["MSFT", "GOOGL", "META"], kt, a.since)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "reaction_summary.md").write_text(render_summary(conn, rows, kt))
    (out / "reaction_summary.json").write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} events -> {out / 'reaction_summary.md'}")


def cmd_expect(a):
    from .expect import consensus, forward, guidance, implied, link
    conn = connect(a.db)
    init_db(conn)
    store = _store(conn)
    from .config import SEC_CONTACT_CONFIGURED
    if not SEC_CONTACT_CONFIGURED:
        print("note: SEC contact not configured (.env); EDGAR requests use a placeholder User-Agent", file=sys.stderr)
    with ingest_run(conn, "expect:" + ",".join(a.what), {"what": a.what}) as (run_id, stats):
        for c in companies(conn, a.tickers):
            if "consensus" in a.what:
                print(f"[{c['ticker']}] archived consensus (Wayback/MarketWatch)")
                consensus.ingest(conn, store, c, run_id, stats)
                conn.commit()
            if "forward" in a.what:
                print(f"[{c['ticker']}] forward consensus snapshot")
                forward.snapshot(conn, store, c, run_id, stats)
                conn.commit()
            if "forward_replay" in a.what:
                forward.replay(conn, store, c, run_id, stats)
                conn.commit()
            if "guidance" in a.what:
                print(f"[{c['ticker']}] management guidance")
                guidance.ingest(conn, store, c, run_id, stats)
                conn.commit()
        if "registry" in a.what:
            link.import_registry(conn, run_id, stats)
            conn.commit()
        if "implied" in a.what:
            implied.ingest(conn, store, run_id, stats)
            conn.commit()
    print(json.dumps({k: v for k, v in stats.items() if not isinstance(v, list)}, indent=1, sort_keys=True))


def cmd_expect_report(a):
    from pathlib import Path
    from .expect import link, report
    from .timeutil import utcnow
    conn = connect(a.db)
    kt = parse_cutoff(a.knowledge_time) if a.knowledge_time else utcnow()
    store = _store(conn, online=not a.offline)
    rows = link.build(conn, kt, store=store)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    desc = link.describe(rows)
    hyp = report.hypotheses(conn, parse_cutoff(a.cutoff) if a.cutoff else kt, kt)
    aud = report.audit(conn, rows, store, refetch=not a.offline)
    (out / "events.json").write_text(json.dumps(rows, indent=1, default=str))
    (out / "descriptive.json").write_text(json.dumps(desc, indent=1, default=str))
    (out / "hypotheses.json").write_text(json.dumps(hyp, indent=1, default=str))
    (out / "audit.json").write_text(json.dumps(aud, indent=1, default=str))
    (out / "coverage.md").write_text("# Expectations coverage\n\nKnowledge time " + kt + "\n\n" +
                                     report.render_coverage(rows) + "\n")
    (out / "events.md").write_text("# Expectations linked to reactions (all earnings releases since 2016)\n\n"
                                   + report.render_events(rows) + "\n")
    print(f"{len(rows)} events -> {out}")
    print(json.dumps(desc, indent=1, default=str))
    print("leakage violations:", len(aud["leakage_violations"]), "| C/D usable:", aud["c_or_d_with_available_at"])


def cmd_valuation(a):
    from pathlib import Path
    from .expect import valuation as V
    from .timeutil import utcnow
    conn = connect(a.db)
    kt = parse_cutoff(a.knowledge_time) if a.knowledge_time else utcnow()
    cut = parse_cutoff(a.cutoff)
    out = {"cutoff": cut, "knowledge_time": kt, "companies": {}}
    for c in companies(conn):
        inp = V.inputs_at(conn, c, cut, kt)
        if inp["missing"]:
            out["companies"][c["ticker"]] = {"status": "not_computed", "missing": inp["missing"], "flags": inp["flags"]}
            continue
        out["companies"][c["ticker"]] = {"status": "ok", "inputs": inp, "wacc": V.wacc(inp),
                                         "base": V.implied_growth(inp), "sensitivities": V.sensitivities(inp),
                                         "bridge": V.bridge(inp)}
    p = Path(a.out)
    p.mkdir(parents=True, exist_ok=True)
    f = p / f"valuation_{a.cutoff[:10]}.json"          # cutoff as given (New York date)
    f.write_text(json.dumps(out, indent=1, default=str, sort_keys=True))
    for tk, v in out["companies"].items():
        if v["status"] != "ok":
            print(tk, "not computed:", v["missing"])
            continue
        print(f"{tk}: WACC {v['wacc']['wacc']:.2%}  implied revenue growth y1-5 {v['base']['implied_revenue_cagr_y1_5']:.1%}")
    print(f"-> {f}")


def cmd_expect_quality(a):
    from pathlib import Path
    from .expect import link, quality
    from .timeutil import utcnow
    conn = connect(a.db)
    kt = parse_cutoff(a.knowledge_time) if a.knowledge_time else utcnow()
    rows = link.build(conn, kt, store=_store(conn, online=False))
    rep = quality.report(conn, rows)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "quality.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / "quality.md").write_text(f"Knowledge time {kt}.\n\n" + quality.render(rep))
    print(json.dumps(rep["reconciliation"], indent=1, default=str))
    for k, v in rep["samples"].items():
        print(f"{k:60s} n={v['n']:3d} rho={v.get('spearman', float('nan')):+.2f} p={v.get('perm_p_two_sided', float('nan')):.3f}")


def cmd_status(a):
    conn = connect(a.db)
    for t in ("source_document", "filing", "observation", "price_bar", "corporate_action", "macro_observation",
              "event", "document_chunk", "derived_metric", "snapshot", "qa_result", "data_gap"):
        print(f"{t:20s} {conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>10,}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="iie", description="Investment Intelligence Engine — data foundation")
    p.add_argument("--db", default=str(DB_PATH))
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init").set_defaults(fn=cmd_init)
    pi = sp.add_parser("ingest")
    pi.add_argument("what", nargs="+", choices=["all", "sec", "docs", "releases", "prices", "events", "macro",
                                                   "context", "benchmarks", "macro_events", "crosscheck",
                                                   "expectations"])
    pi.add_argument("--tickers", nargs="*")
    pi.add_argument("--since", default="2016-01-01", help="start of price/macro/event history")
    pi.add_argument("--docs-since", default="2023-01-01", help="first filing date for full-document download")
    pi.add_argument("--vintage-start", default="2019-01-31", help="first month-end ALFRED vintage")
    pi.add_argument("--vintage", nargs="*", help="extra exact ALFRED vintage dates (e.g. snapshot cutoffs)")
    pi.set_defaults(fn=cmd_ingest)
    pr = sp.add_parser("rebuild", help="rebuild --db from the evidence store of --from-db, offline")
    pr.add_argument("--from-db", required=True)
    pr.set_defaults(fn=cmd_rebuild)
    sp.add_parser("qa").set_defaults(fn=cmd_qa)
    ps = sp.add_parser("snapshot")
    ps.add_argument("--cutoff", required=True, help="YYYY-MM-DD (= 23:59:59 New York) or ISO timestamp")
    ps.add_argument("--knowledge-time", help="system-time cutoff on retrieved_at (default: now)")
    ps.add_argument("--tickers", nargs="*")
    ps.set_defaults(fn=cmd_snapshot)
    pv = sp.add_parser("verify")
    pv.add_argument("--snapshot", type=int, required=True)
    pv.set_defaults(fn=cmd_verify)
    pq = sp.add_parser("search")
    pq.add_argument("query")
    pq.add_argument("--ticker")
    pq.add_argument("--as-of")
    pq.add_argument("--limit", type=int, default=6)
    pq.set_defaults(fn=cmd_search)
    pc = sp.add_parser("case", help="event case study(ies) for earnings releases, by accession number")
    pc.add_argument("accession", nargs="+")
    pc.add_argument("--out", default="docs/event_engine")
    pc.add_argument("--knowledge-time")
    pc.set_defaults(fn=cmd_case)
    pr2 = sp.add_parser("reactions", help="cross-event earnings reaction summary")
    pr2.add_argument("--tickers", nargs="*")
    pr2.add_argument("--since", default="2016-01-01")
    pr2.add_argument("--out", default="docs/event_engine")
    pr2.add_argument("--knowledge-time")
    pr2.set_defaults(fn=cmd_reactions)
    pe = sp.add_parser("expect", help="Expectations Engine ingestion")
    pe.add_argument("what", nargs="+", choices=["consensus", "guidance", "registry", "implied", "forward",
                                                     "forward_replay"])
    pe.add_argument("--tickers", nargs="*")
    pe.set_defaults(fn=cmd_expect)
    per = sp.add_parser("expect-report", help="coverage, linked event table, hypotheses and audit")
    per.add_argument("--out", default="docs/expectations")
    per.add_argument("--knowledge-time")
    per.add_argument("--cutoff", help="cutoff for the market-vs-fundamentals hypotheses (default: knowledge time)")
    per.add_argument("--offline", action="store_true", help="skip the archive re-fetch audit")
    per.set_defaults(fn=cmd_expect_report)
    pq2 = sp.add_parser("expect-quality", help="consensus/EPS comparability and statistical-quality report")
    pq2.add_argument("--out", default="docs/expectations")
    pq2.add_argument("--knowledge-time")
    pq2.set_defaults(fn=cmd_expect_quality)
    pvv = sp.add_parser("valuation", help="reverse DCF v2 at a cutoff (inputs, WACC, implied growth, sensitivities)")
    pvv.add_argument("--cutoff", required=True)
    pvv.add_argument("--knowledge-time")
    pvv.add_argument("--out", default="docs/valuation")
    pvv.set_defaults(fn=cmd_valuation)
    sp.add_parser("status").set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
