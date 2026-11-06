"""Event Reaction Engine and expectations: adversarial tests on a synthetic market with known answers."""
import json
import random
import sqlite3
from datetime import date, timedelta

import pytest

from iie.db import init_db
from iie.expectations import grade, ingest_registry, normalise
from iie.reaction import PriceSeries, classify_timing, measure, window_return
from iie.timeutil import et_to_utc_str

KT = "2099-01-01T00:00:00Z"


def bdays(start, n):
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DAYS = bdays("2023-01-02", 320)          # synthetic calendar (no holidays)
D0 = DAYS[300]                            # reaction day used in tests
ANNOUNCE = DAYS[299]                      # after-close announcement the evening before


def _doc(conn, store, name="x"):
    return store.register(f"https://example.test/{name}", name.encode(), source_system="sec_edgar",
                          doc_type="t", retrieved_at="2026-01-01T00:00:00Z").doc_id


def _cid(conn, tk):
    return conn.execute("SELECT company_id FROM company WHERE ticker=?", (tk,)).fetchone()[0]


def add_bars(conn, doc, tk, closes, opens=None, retrieved="2026-01-01T00:00:00Z", days=DAYS):
    cid = _cid(conn, tk)
    for i, c in enumerate(closes):
        d = days[i]
        o = opens[i] if opens else c
        conn.execute("""INSERT INTO price_bar(company_id, provider, trade_date, open, high, low, close, adj_close,
            volume, adjustment_basis, available_at, availability_basis, source_doc_id, retrieved_at, natural_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (cid, "yahoo_chart", d, o, max(o, c), min(o, c), c, c, 1e6, "test",
                      et_to_utc_str(date.fromisoformat(d), 16, 0), "test", doc, retrieved, f"bar|{tk}|{d}|{retrieved}"))


def add_event(conn, doc, tk, etype, public_at, nk, details=None):
    cid = None if tk is None else _cid(conn, tk)
    cur = conn.execute("""INSERT INTO event(company_id, event_type, event_date, available_at, availability_basis,
        accession_no, details_json, source_doc_id, natural_key) VALUES (?,?,?,?,?,?,?,?,?)""",
                       (cid, etype, public_at[:10], public_at, "test", nk, json.dumps(details or {}), doc, nk))
    return cur.lastrowid


@pytest.fixture
def market(env):
    """SPY random walk; MSFT = 1.5 x SPY + noise, with a known shock on D0."""
    conn, store, tmp = env
    doc = _doc(conn, store)
    rnd = random.Random(7)
    spy, msft = [100.0], [200.0]
    for i in range(1, len(DAYS)):
        rm = rnd.gauss(0.0003, 0.01)
        rs = 0.0001 + 1.5 * rm + rnd.gauss(0, 0.008)
        if DAYS[i] == D0:
            rs = 1.5 * rm + 0.05                    # +5% abnormal on the reaction day
        spy.append(spy[-1] * (1 + rm))
        msft.append(msft[-1] * (1 + rs))
    opens = [c for c in msft]
    i0 = DAYS.index(D0)
    opens[i0] = msft[i0 - 1] * 1.03               # gap up 3% at the open of D0
    for tk, series in (("SPY", spy), ("QQQ", spy), ("^GSPC", spy), ("^IXIC", spy), ("XLK", spy)):
        add_bars(conn, doc, tk, series)
    add_bars(conn, doc, "MSFT", msft, opens)
    eid = add_event(conn, doc, "MSFT", "earnings_release", et_to_utc_str(date.fromisoformat(ANNOUNCE), 16, 5),
                    "0000000000-24-000001", {"items": {"2.02": "Results"}})
    return conn, store, doc, eid, spy, msft, opens


def test_timing_classification():
    cal = DAYS
    assert classify_timing(et_to_utc_str(date.fromisoformat(DAYS[10]), 16, 5), cal)[:2] == (DAYS[11], "after_close")
    assert classify_timing(et_to_utc_str(date.fromisoformat(DAYS[10]), 8, 0), cal)[:2] == (DAYS[10], "pre_market")
    assert classify_timing(et_to_utc_str(date.fromisoformat(DAYS[10]), 12, 0), cal)[:2] == (DAYS[10], "intraday")
    sat = (date.fromisoformat(DAYS[4]) + timedelta(days=1)).isoformat()    # DAYS[4] is a Friday
    assert classify_timing(et_to_utc_str(date.fromisoformat(sat), 10, 0), cal)[:2] == (DAYS[5], "non_trading_day")


def test_windows_and_abnormal_returns_match_hand_calculation(market):
    conn, store, doc, eid, spy, msft, opens = market
    r = measure(conn, eid, KT, expectations=False)
    i0 = DAYS.index(D0)
    assert r["timing"] == "after_close" and r["reaction_day"] == D0
    w = r["windows"]
    assert w["d0"]["return"] == pytest.approx(msft[i0] / msft[i0 - 1] - 1)
    assert w["overnight_gap"]["return"] == pytest.approx(0.03)
    assert w["session_d0"]["return"] == pytest.approx(msft[i0] / opens[i0] - 1)
    assert w["d0"]["benchmarks"]["SPY"]["excess"] == pytest.approx(w["d0"]["return"] - (spy[i0] / spy[i0 - 1] - 1))
    mm = r["market_model"]
    assert mm["beta"] == pytest.approx(1.5, abs=0.15)
    assert w["d0"]["market_model"]["car"] == pytest.approx(0.05, abs=0.02)
    assert w["d0"]["market_model"]["scar"] > 3
    # index benchmarks never used for open-based windows
    assert "^GSPC" not in w["overnight_gap"]["benchmarks"] and "XLK" in w["overnight_gap"]["benchmarks"]


def test_estimation_window_excludes_event_and_future(market):
    conn, store, doc, eid, spy, msft, opens = market
    base = measure(conn, eid, KT, expectations=False)["market_model"]
    assert base["last"] < DAYS[DAYS.index(D0) - 10]
    # adversarial: corrupt prices inside the excluded pre-event gap and after the event, in a LATER retrieval
    cid = _cid(conn, "MSFT")
    for d in DAYS[DAYS.index(D0) - 9:]:
        conn.execute("""INSERT INTO price_bar(company_id, provider, trade_date, open, high, low, close, adj_close, volume,
            adjustment_basis, available_at, availability_basis, source_doc_id, retrieved_at, natural_key)
            SELECT company_id, provider, trade_date, open*3, high*3, low*3, close*3, adj_close, volume, adjustment_basis,
            available_at, availability_basis, source_doc_id, '2027-01-01T00:00:00Z', natural_key||'|x'
            FROM price_bar WHERE company_id=? AND trade_date=?""", (cid, d))
    again = measure(conn, eid, KT, expectations=False)["market_model"]
    assert again == base


def test_later_retrieval_does_not_change_result_at_earlier_knowledge_time(market):
    conn, store, doc, eid, *_ = market
    kt = "2026-06-01T00:00:00Z"
    h1 = measure(conn, eid, kt, expectations=False)["content_sha256"]
    rewritten = [1.0 + 0.01 * ((i * 7) % 5) for i in range(len(DAYS))]
    add_bars(conn, doc, "MSFT", rewritten, retrieved="2026-07-01T00:00:00Z")   # vendor rewrite later
    assert measure(conn, eid, kt, expectations=False)["content_sha256"] == h1
    assert measure(conn, eid, "2026-08-01T00:00:00Z", expectations=False)["content_sha256"] != h1


def test_incomplete_bar_is_ignored(market):
    conn, store, doc, eid, *_ = market
    cid = _cid(conn, "MSFT")
    d = DAYS[-1]
    # a bar retrieved mid-session (before its 16:00 close) with an absurd price
    conn.execute("""INSERT INTO price_bar(company_id, provider, trade_date, open, high, low, close, adj_close, volume,
        adjustment_basis, available_at, availability_basis, source_doc_id, retrieved_at, natural_key)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (cid, "yahoo_chart", d, 1, 1, 1, 1, 1, 1, "t",
                                                   et_to_utc_str(date.fromisoformat(d), 16, 0), "t", doc,
                                                   et_to_utc_str(date.fromisoformat(d), 11, 0), "partial"))
    s = PriceSeries(conn, cid, KT)
    assert s.point(d, "close") != 1


def test_dividend_and_split_handling():
    class S(PriceSeries):
        def __init__(self, bars, div):
            self.bars = {d: {"open": o, "close": c} for d, o, c in bars}
            self.dates = [b[0] for b in bars]
            self.div = div
    cal = ["2024-01-02", "2024-01-03", "2024-01-04"]
    # vendor split-adjusted closes are continuous across a split; dividend 1.0 on 01-03
    s = S([("2024-01-02", 100, 100), ("2024-01-03", 99, 99), ("2024-01-04", 99, 101)], {"2024-01-03": 1.0})
    assert window_return(s, cal, ("2024-01-02", "close"), ("2024-01-03", "close")) == pytest.approx(0.0)
    assert window_return(s, cal, ("2024-01-02", "close"), ("2024-01-03", "open")) == pytest.approx(0.0)
    assert window_return(s, cal, ("2024-01-02", "close"), ("2024-01-04", "close")) == pytest.approx(101 / 99 - 1)


def test_overlap_and_attribution(market):
    conn, store, doc, eid, *_ = market
    assert measure(conn, eid, KT, expectations=False)["attribution_uncertainty"]["level"] == "low"
    add_event(conn, doc, "GOOGL", "earnings_release", et_to_utc_str(date.fromisoformat(ANNOUNCE), 16, 1), "g1")
    r = measure(conn, eid, KT, expectations=False)
    assert r["attribution_uncertainty"]["level"] == "medium"
    assert any(o["ticker"] == "GOOGL" and o["within"] == "d0_reaction_period" for o in r["overlapping_events"])
    # a macro release at 14:00 on d0 makes attribution 'high'
    fomc_doc = store.register("https://example.test/fomc", b"<p>For release at 2:00 p.m. EST</p><p>the Committee "
                              b"decided to maintain the target range for the federal funds rate at 5-1/4 to 5-1/2 "
                              b"percent.</p>", source_system="federal_reserve", doc_type="t").doc_id
    add_event(conn, fomc_doc, None, "fomc_statement", et_to_utc_str(date.fromisoformat(D0), 14, 0), "f1")
    r = measure(conn, eid, KT, expectations=False, store=store)
    assert r["attribution_uncertainty"]["level"] == "high"
    fomc = [o for o in r["overlapping_events"] if o["event_type"] == "fomc_statement"][0]
    assert "maintain 5-1/4 to 5-1/2" in fomc["label"]
    # an event published after d0's close but reacting on d0+1 is outside the d0 period
    add_event(conn, doc, "AMZN", "earnings_release", et_to_utc_str(date.fromisoformat(D0), 16, 5), "a1")
    r = measure(conn, eid, KT, expectations=False, store=store)
    amzn = [o for o in r["overlapping_events"] if o["ticker"] == "AMZN"][0]
    assert amzn["within"] == "d1_to_d4"


def test_expectation_grading_rules():
    ev = "2024-01-30T21:03:17Z"
    assert grade("sec_edgar", True, "2023-10-24T20:00:00Z", ev) == "A"
    assert grade("press", True, "2024-01-29T12:00:00Z", ev) == "B"
    assert grade("press", True, "2024-01-30T21:30:00Z", ev) == "C"       # published after the release
    assert grade("press", False, "2024-01-29T12:00:00Z", ev) == "D"      # quote not found
    assert grade("issuer_ir", True, None, ev) == "D"                       # undated
    assert normalise("Azure growth of 26%–27%") == normalise("azure growth of 26%-27%")


def test_registry_verification_and_point_in_time_use(market, tmp_path):
    conn, store, doc, eid, *_ = market
    from conftest import FakeTransport
    pre = ('<html><head><meta property="article:published_time" content="2024-01-25T10:00:00Z"></head>'
           '<body><p>Analysts expect revenue of $61.1 billion for the quarter.</p></body></html>')
    post = ('<html><head><meta property="article:published_time" content="2024-01-31T01:00:00Z"></head>'
            '<body><p>versus $61.12 billion expected by LSEG.</p></body></html>')
    store.transport = FakeTransport({"preview": pre, "results": post, "missing": "<p>nothing here</p>"})
    reg = tmp_path / "reg.json"
    public_at = et_to_utc_str(date.fromisoformat(ANNOUNCE), 16, 5)
    base = {"ticker": "MSFT", "event_key": "MSFT:FY2024Q2", "expectation_type": "sell_side_consensus",
            "metric": "revenue", "unit": "USD", "event_public_at": public_at}
    reg.write_text(json.dumps([
        dict(base, provider="Zacks", source_kind="press", source_url="https://n.test/preview",
             verbatim_quote="expect revenue of $61.1 billion", value_point=61.1e9,
             source_published_at="2023-01-01T00:00:00Z"),        # wrong registry date: metadata must win
        dict(base, provider="LSEG", source_kind="press", source_url="https://n.test/results",
             verbatim_quote="$61.12 billion expected by LSEG", value_point=61.12e9),
        dict(base, provider="Made-up", source_kind="press", source_url="https://n.test/missing",
             verbatim_quote="consensus of $70 billion", value_point=70e9, source_published_at="2024-01-01T00:00:00Z"),
    ]))
    ingest_registry(conn, store, None, {}, path=reg)
    rows = {r["provider"]: r for r in conn.execute("SELECT * FROM expectation")}
    assert rows["Zacks"]["source_published_at"] == "2024-01-25T10:00:00Z"
    assert "disagrees with page metadata" in rows["Zacks"]["notes"]
    # ANNOUNCE is 2024-03-xx in the synthetic calendar, so the 2024-01-25 preview is before the event
    assert rows["Zacks"]["provenance_grade"] == "B" and rows["Zacks"]["available_at"]
    assert rows["Made-up"]["quote_verified"] == 0 and rows["Made-up"]["provenance_grade"] == "D"
    assert rows["Made-up"]["available_at"] is None


def test_migration_from_v1_schema(tmp_path):
    p = tmp_path / "v1.sqlite"
    c = sqlite3.connect(str(p))
    c.executescript("""CREATE TABLE company (company_id INTEGER PRIMARY KEY, ticker TEXT NOT NULL UNIQUE,
        cik TEXT NOT NULL UNIQUE, name TEXT NOT NULL, fiscal_year_end TEXT NOT NULL, exchange TEXT);
        INSERT INTO company VALUES (1,'GOOGL','0001652044','Alphabet Inc.','1231','NASDAQ');""")
    c.commit()
    c.close()
    from iie.db import connect
    conn = connect(p)
    init_db(conn)
    r = conn.execute("SELECT * FROM company WHERE ticker='GOOGL'").fetchone()
    assert r["company_id"] == 1 and r["role"] == "universe" and r["fiscal_year_end"] == "1231"
    assert conn.execute("SELECT count(*) FROM company WHERE role='benchmark' AND cik IS NULL").fetchone()[0] == 6
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_page_rewritten_after_event_is_not_point_in_time(market, tmp_path):
    """CNBC-style 'results vs expectations' page: first published before the release, rewritten after."""
    conn, store, doc, eid, *_ = market
    from conftest import FakeTransport
    public_at = et_to_utc_str(date.fromisoformat(ANNOUNCE), 16, 5)
    page = ('<html><head><meta property="article:published_time" content="2024-01-02T17:00:00Z">'
            '<meta property="article:modified_time" content="2099-01-01T00:00:00Z"></head>'
            '<body><p>Revenue: $62.02 billion, vs. $61.12 billion expected</p></body></html>')
    store.transport = FakeTransport({"cnbc": page})
    reg = tmp_path / "r.json"
    reg.write_text(json.dumps([{"ticker": "MSFT", "event_key": "MSFT:FY2024Q2", "expectation_type": "sell_side_consensus",
                                "metric": "revenue", "provider": "LSEG", "source_kind": "press",
                                "source_url": "https://x.test/cnbc", "event_public_at": public_at, "unit": "USD",
                                "verbatim_quote": "vs. $61.12 billion expected", "value_point": 61.12e9}]))
    ingest_registry(conn, store, None, {}, path=reg)
    r = conn.execute("SELECT * FROM expectation").fetchone()
    assert r["provenance_grade"] == "C" and r["available_at"] is None
    assert "last modified" in r["notes"]


def test_event_without_expectations_reports_absence(market):
    conn, store, doc, eid, *_ = market
    res = measure(conn, eid, KT, store=store)
    ex = res["expectations"]
    assert ex["point_in_time"] == [] and ex["post_event_documented"] == []
    assert "none fabricated" in ex["note"]
