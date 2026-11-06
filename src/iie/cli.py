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
    sp.add_parser("status").set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
