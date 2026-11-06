"""Offline integration tests: full pipeline on synthetic sources through the real ingest code paths."""
import json

import pytest

from conftest import FakeTransport
from iie.db import companies, ingest_run
from iie.ingest import documents, earnings_release, events, macro, prices, sec
from iie.qa import run_all
from iie.search import search
from iie.snapshot import build_snapshot, content_hash, create_snapshot, verify_snapshot
from iie.timeutil import parse_cutoff


@pytest.fixture
def loaded(env, routes, monkeypatch):
    conn, store, tmp = env
    store.transport = FakeTransport(routes)
    monkeypatch.setattr(macro, "FRED_DAILY_SERIES", {"DGS10": ("Percent", "10y CMT")})
    monkeypatch.setattr(macro, "ALFRED_REVISED_SERIES", {"CPIAUCSL": ("Index", "CPI")})
    import iie.snapshot as snap
    monkeypatch.setattr(snap, "FRED_DAILY_SERIES", {"DGS10": ("Percent", "10y CMT")})
    monkeypatch.setattr(snap, "ALFRED_REVISED_SERIES", {"CPIAUCSL": ("Index", "CPI")})
    with ingest_run(conn, "test", {}) as (run_id, stats):
        c = companies(conn, ["GOOGL"])[0]
        sec.ingest_filings(conn, store, c, run_id, stats)
        sec.ingest_companyfacts(conn, store, c, run_id, stats)
        c = companies(conn, ["GOOGL"])[0]
        documents.ingest_filing_documents(conn, store, c, "2023-01-01", run_id, stats)
        earnings_release.ingest_releases(conn, c, run_id, stats, store)
        prices.ingest_prices(conn, store, c, "2022-01-01", run_id, stats)
        events.build_events(conn, c, "2022-01-01", run_id, stats)
        macro.ingest_treasury(conn, store, [2024], run_id, stats)
        macro.ingest_fred_daily(conn, store, "2024-01-01", run_id, stats)
        macro.ingest_alfred(conn, store, ["2023-12-31", "2024-01-31"], "2023-01-01", run_id, stats)
    return conn, store, tmp


def snap(conn, cutoff, kt="2099-01-01T00:00:00Z"):
    return build_snapshot(conn, parse_cutoff(cutoff), kt, ["GOOGL"])["companies"]["GOOGL"]


def test_release_window_uses_press_release_then_xbrl(loaded):
    conn, *_ = loaded
    # 17:30 ET on 2024-01-30: release accepted 16:01 ET, 10-K accepted 21:43 ET -> release only
    g = snap(conn, "2024-01-30T17:30:00")
    rev = g["income_and_cash_flow"]["revenue"]["latest_quarter"]
    assert g["latest_fiscal_quarter"] == "FY2023 Q4"
    assert rev["value"] == 130e6 and rev["source_kind"] == "earnings_release_table"
    assert rev["reliability"] == "primary_issuer_unstructured"
    assert g["income_and_cash_flow"]["revenue"]["ttm"]["value"] == 460e6
    # one minute before the release: Q3 is the latest quarter
    g0 = snap(conn, "2024-01-30T16:00:00")
    assert g0["latest_fiscal_quarter"] == "FY2023 Q3"
    # after the 10-K: Q4 derived from XBRL (FY - 9M) supersedes the release
    g2 = snap(conn, "2024-02-01")
    rev2 = g2["income_and_cash_flow"]["revenue"]["latest_quarter"]
    assert rev2["kind"] == "derived" and rev2["value"] == 130e6
    assert rev2["methodology"].startswith("Q4 = FY - YTD9")
    ids = {i["id"] for i in rev2["inputs"]}
    assert len(ids) == 2


def test_restatement_is_point_in_time(loaded):
    conn, *_ = loaded
    before = snap(conn, "2024-04-01")    # Q1 2023 as first reported
    after = snap(conn, "2024-05-01")     # Q1 2024 10-Q restates the comparative to 101
    q1_before = [f for f in [before["income_and_cash_flow"]["revenue"]["same_quarter_prior_year"]] if f]
    assert after["latest_fiscal_quarter"] == "FY2024 Q1"
    pq = after["income_and_cash_flow"]["revenue"]["same_quarter_prior_year"]
    assert pq["value"] == 101e6
    assert pq["revision_history_at_cutoff"]["revised"] is True
    assert pq["revision_history_at_cutoff"]["first_reported_value"] == 100e6
    # at the earlier cutoff, the TTM uses the original 100
    assert before["income_and_cash_flow"]["revenue"]["ttm"]["value"] == 460e6
    assert q1_before == [] or q1_before[0]["value"] != 101e6


def test_cash_flow_quarter_derived_from_ytd(loaded):
    conn, *_ = loaded
    g = snap(conn, "2023-08-01")
    cfo = g["income_and_cash_flow"]["operating_cash_flow"]["latest_quarter"]
    assert cfo["kind"] == "derived" and cfo["value"] == 35e6
    assert "YTD6 - Q1" in cfo["methodology"]


def test_eps_q4_is_not_derived(loaded):
    conn, *_ = loaded
    g = snap(conn, "2024-02-01")
    # the release EPS (1.64) remains the only Q4 EPS: EPS is not additive, so FY - 9M is never computed
    eps = g["income_and_cash_flow"]["eps_diluted"]["latest_quarter"]
    assert eps["source_kind"] == "earnings_release_table" and eps["value"] == 1.64


def test_prices_are_as_traded_at_cutoff(loaded):
    conn, *_ = loaded
    from iie.snapshot import _market
    from iie.derive import FiscalIndex
    c = companies(conn, ["GOOGL"])[0]
    gaps = []
    m = _market(conn, c, parse_cutoff("2022-07-15"), "2099-01-01T00:00:00Z", FiscalIndex({}), gaps)
    assert m["last_close"]["trade_date"] == "2022-07-15"
    assert m["last_close"]["value"] == 2240.0           # 112 (vendor, split-adjusted) x 20
    # the split is not 'known' at that cutoff
    assert all(a["type"] != "split" for a in m["corporate_actions_known"])
    m2 = _market(conn, c, parse_cutoff("2022-07-18"), "2099-01-01T00:00:00Z", FiscalIndex({}), gaps)
    assert m2["last_close"]["value"] == 113.0
    assert any(a["type"] == "split" for a in m2["corporate_actions_known"])
    # bar for 2024-01-30 is visible only after its 16:00 ET close
    m3 = _market(conn, c, parse_cutoff("2024-01-30T15:59:00"), "2099-01-01T00:00:00Z", FiscalIndex({}), gaps)
    assert m3["last_close"]["trade_date"] == "2024-01-29"


def test_macro_point_in_time(loaded):
    conn, *_ = loaded
    s = build_snapshot(conn, parse_cutoff("2024-01-30T17:30:00"), "2099-01-01T00:00:00Z", ["GOOGL"])
    assert s["macro"]["treasury_par_curve"]["10 Yr"]["obs_date"] == "2024-01-29"   # 01-30 curve not yet out
    assert s["macro"]["fred"]["DGS10"]["obs_date"] == "2024-01-29"                  # next-business-day lag
    assert s["macro"]["fred"]["CPIAUCSL"]["obs_date"] == "2023-11-01"               # Dec CPI not in 2023-12-31 vintage
    s2 = build_snapshot(conn, parse_cutoff("2024-02-01"), "2099-01-01T00:00:00Z", ["GOOGL"])
    assert s2["macro"]["fred"]["CPIAUCSL"]["obs_date"] == "2023-12-01"
    assert s2["macro"]["fred"]["CPIAUCSL"]["vintage_date"] == "2024-01-31"


def test_snapshot_is_reproducible_and_immutable(loaded, routes):
    conn, store, tmp = loaded
    kt = "2099-01-01T00:00:00Z"
    sid, path, h, content = create_snapshot(conn, parse_cutoff("2024-01-30T17:30:00"), kt, ["GOOGL"],
                                            out_dir=tmp / "snaps")
    assert content["lookahead_audit"]["status"] == "pass"
    assert content["lookahead_audit"]["items_checked"] > 20
    res = verify_snapshot(conn, sid, store, out_dir=tmp / "snaps")
    assert res["file_hash_matches"] and res["rebuild_hash_matches"] and res["evidence_bytes_verified"]
    with pytest.raises(Exception, match="immutable"):
        conn.execute("UPDATE snapshot SET cutoff='x' WHERE snapshot_id=?", (sid,))
    n_items = conn.execute("SELECT count(*) FROM snapshot_item WHERE snapshot_id=?", (sid,)).fetchone()[0]
    assert n_items > 10
    assert conn.execute("SELECT count(*) FROM derived_metric WHERE snapshot_id=?", (sid,)).fetchone()[0] > 0


def test_later_ingest_does_not_change_earlier_knowledge_time(loaded, routes):
    conn, store, tmp = loaded
    from iie.timeutil import utcnow
    kt = utcnow()
    cutoff = parse_cutoff("2024-02-01")
    h1 = content_hash(build_snapshot(conn, cutoff, kt, ["GOOGL"]))
    # vendor silently rewrites history in a later retrieval
    import time
    time.sleep(1.1)
    routes["finance/chart/GOOGL"] = lambda u: json.dumps(json.loads(_chart_rewritten()))
    with ingest_run(conn, "test2", {}) as (run_id, stats):
        prices.ingest_prices(conn, store, companies(conn, ["GOOGL"])[0], "2022-01-01", run_id, stats)
    assert content_hash(build_snapshot(conn, cutoff, kt, ["GOOGL"])) == h1
    assert content_hash(build_snapshot(conn, cutoff, utcnow(), ["GOOGL"])) != h1


def _chart_rewritten():
    from conftest import yahoo_chart_json
    d = json.loads(yahoo_chart_json())
    d["chart"]["result"][0]["indicators"]["quote"][0]["close"][-2] = 999.0
    d["chart"]["result"][0]["indicators"]["quote"][0]["close"][-1] = 999.0
    return json.dumps(d)


def test_qa_passes_on_clean_data_and_reconciles_release(loaded):
    conn, store, tmp = loaded
    with ingest_run(conn, "qa", {}) as (run_id, stats):
        summary = run_all(conn, store, run_id)
    fails = {k: v for k, v in summary.items() if v.get("fail")}
    assert not fails, fails
    rec = json.loads(conn.execute("SELECT detail_json FROM qa_result WHERE check_name='earnings_release_vs_xbrl' "
                                  "AND severity='pass'").fetchone()[0])
    assert rec["reconciled_exact"] == 2          # revenue and net income vs FY - 9M from the 10-K
    assert rec["awaiting_xbrl"] == 2       # operating income (absent from fixture XBRL) and EPS (Q4 not derivable)
    assert summary["acceptance_crosscheck"].get("pass") == 1
    assert summary["split_vs_xbrl"].get("pass") == 1


def test_qa_detects_release_mismatch_and_bad_timestamps(loaded):
    conn, store, tmp = loaded
    c = companies(conn, ["GOOGL"])[0]
    doc = conn.execute("SELECT doc_id FROM source_document LIMIT 1").fetchone()[0]
    conn.execute("""INSERT INTO observation(company_id, source_kind, taxonomy, concept, accounting_definition, value,
        unit, period_start, period_end, duration_days, fiscal_year, fiscal_period, source_doc_id, source_url,
        available_at, availability_basis, availability_verified, retrieved_at, reliability, extraction_method,
        natural_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (c["company_id"], "earnings_release_table", "press_release", "net_income", "test", 35e6, "USD",
                  "2023-10-01", "2023-12-31", 91, 2023, "Q4", doc, "u", "2023-12-15T00:00:00Z", "test", 1,
                  "2024-01-01T00:00:00Z", "test", "test", "bad"))
    with ingest_run(conn, "qa", {}) as (run_id, stats):
        summary = run_all(conn, store, run_id)
    assert summary["earnings_release_vs_xbrl"].get("fail", 0) >= 1
    assert summary["available_before_period_end"].get("fail", 0) >= 1


def test_search_returns_citations_and_respects_as_of(loaded):
    conn, *_ = loaded
    hits = search(conn, "cloud competition", "GOOGL")
    assert hits and hits[0]["citation"]["sha256"] and hits[0]["citation"]["url"].startswith("https://www.sec.gov/")
    assert "Item 1A. Risk Factors" in hits[0]["section"]
    early = search(conn, "cloud competition", "GOOGL", as_of=parse_cutoff("2023-05-01"))
    assert {h["citation"]["public_at"] for h in early} == {"2023-04-25T20:30:00Z"}
    rel = search(conn, "Fourth Quarter 2023 Results", "GOOGL", as_of=parse_cutoff("2024-01-30T16:00:00"))
    assert rel == []


def test_events_have_acceptance_timestamps(loaded):
    conn, *_ = loaded
    rows = conn.execute("SELECT event_type, available_at FROM event ORDER BY available_at").fetchall()
    types = {r["event_type"] for r in rows}
    assert {"earnings_release", "periodic_report", "split_ex_date"} <= types
    er = [r for r in rows if r["event_type"] == "earnings_release"][0]
    assert er["available_at"] == "2024-01-30T21:01:26Z"
