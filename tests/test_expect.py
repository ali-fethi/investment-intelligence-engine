"""Expectations Engine: parsers, point-in-time selection, leakage and revision tests (offline)."""
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from conftest import FakeTransport
from iie.db import set_fiscal_year_end
from iie.expect import consensus, guidance, implied, link
from iie.expect.parsers import parse_marketwatch, parse_marketwatch_old
from iie.expect.store import put
from iie.timeutil import et_to_utc_str

MW_NEW = """<html><body><h2>Quarterly Numbers</h2> Actuals Estimates GOOGL will report Q4 2023 earnings on 02/01/2024
Q4 2022 Q1 2023 Q2 2023 Q3 2023 Estimate 1.18 1.07 1.34 1.45 Actual 1.05 1.17 1.44 1.55 Surprise -0.13 0.10 0.10 0.10
Q4 2023 Q1 2024 High 1.70 1.60 Low 1.50 1.30 Average 1.59 1.45 window.analystChartData = 1;</body></html>"""
MW_AFTER = MW_NEW.replace("Q4 2022 Q1 2023 Q2 2023 Q3 2023 Estimate 1.18 1.07 1.34 1.45 Actual 1.05 1.17 1.44 1.55",
                          "Q1 2023 Q2 2023 Q3 2023 Q4 2023 Estimate 1.07 1.34 1.45 1.59 Actual 1.17 1.44 1.55 1.64")
MW_OLD = """<html><body>Number of Ratings: 49 Current Quarters Estimate: 2.18 FY Report Date: 12 / 2018 Last Quarter's
Earnings: 1.55 Earnings Per Share This Quarter Next Quarter This Fiscal Next Fiscal # of Estimates 40.00 28.00 44.00 47.00
Mean Estimate 2.18 1.57 7.36 7.51 High Estimates 2.39 1.91 7.61 9.10 Low Estimates 1.91 1.22 6.99 6.03 Coefficient Variance
</body></html>"""


def test_marketwatch_parsers():
    p = parse_marketwatch(MW_NEW.encode())
    assert p["forward"]["Q4 2023"] == {"high": 1.70, "low": 1.50, "average": 1.59}
    assert p["history"]["Q3 2023"] == {"estimate": 1.45, "actual": 1.55}
    assert p["next_report"]["quarter"] == "Q4 2023"
    o = parse_marketwatch_old(MW_OLD.encode())
    assert o["this_quarter"] == {"n": 40.0, "mean": 2.18, "high": 2.39, "low": 1.91}
    assert o["last_quarter_eps"] == 1.55


@pytest.fixture
def googl(env):
    conn, store, tmp = env
    c = conn.execute("SELECT * FROM company WHERE ticker='GOOGL'").fetchone()
    set_fiscal_year_end(conn, c["company_id"], "1231")
    doc = store.register("https://example.test/f", b"filing", source_system="sec_edgar", doc_type="t").doc_id
    # earnings release for Q4 2023 on 2024-01-30 16:01 ET; previous (Q3) on 2023-10-24
    for acc, d, h in (("A-23-3", date(2023, 10, 24), 16), ("A-24-1", date(2024, 1, 30), 16)):
        conn.execute("""INSERT INTO event(company_id, event_type, event_date, available_at, availability_basis,
            accession_no, details_json, source_doc_id, natural_key) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (c["company_id"], "earnings_release", d.isoformat(), et_to_utc_str(d, h, 1), "t", acc, "{}", doc,
                      f"filing|{acc}"))
    c = conn.execute("SELECT * FROM company WHERE ticker='GOOGL'").fetchone()
    return conn, store, c, doc


def cdx(rows):
    return "\n".join(f"{ts} https://www.marketwatch.com/investing/stock/googl/analystestimates {dg}"
                     for ts, dg in rows)


def test_consensus_uses_only_pre_release_captures(googl):
    conn, store, c, doc = googl
    routes = {"cdx/search": lambda u: cdx([("20231201120000", "d1"), ("20240129120000", "d2"),
                                         ("20240131120000", "d3")]),
              "20231201120000id_": MW_NEW.replace("1.59", "1.55"), "20240129120000id_": MW_NEW,
              "20240131120000id_": MW_AFTER}
    store.transport = FakeTransport(routes)
    stats = {}
    consensus.ingest(conn, store, c, None, stats)
    rows = conn.execute("SELECT * FROM expectation_obs WHERE target_period='FY2023Q4' AND stat IN "
                        "('mean','mean_at_report')").fetchall()
    pre = [r for r in rows if r["stat"] == "mean"]
    post = [r for r in rows if r["stat"] == "mean_at_report"]
    assert {r["value"] for r in pre} == {1.59, 1.55}
    assert all(r["provenance_grade"] == "B" and r["available_at"] < "2024-01-30T21:01:00Z" for r in pre)
    # the capture made after the release only yields grade-C history, never a point-in-time value
    assert post and all(r["provenance_grade"] == "C" and r["available_at"] is None for r in post)
    street = conn.execute("SELECT * FROM expectation_obs WHERE stat='street_actual' AND target_period='FY2023Q4'").fetchone()
    assert street["value"] == 1.64 and street["available_at"] is None
    # link layer: latest usable mean is the 2024-01-29 capture; revision from 1.55 -> 1.59
    ev = conn.execute("SELECT * FROM event WHERE accession_no='A-24-1'").fetchone()
    row = link.event_row(conn, ev, "2099-01-01T00:00:00Z", store)
    assert row["consensus_eps"] == 1.59 and row["consensus_eps_revision"] == pytest.approx(1.59 / 1.55 - 1)
    assert row["eps_street_actual"] == 1.64


def test_old_layout_requires_previous_quarter_reported(googl):
    conn, store, c, doc = googl
    # captured 2023-10-20, BEFORE the Q3 release: 'This Quarter' is then Q3, not Q4 -> must be skipped for Q4
    routes = {"cdx/search": lambda u: cdx([("20231020120000", "d1")]), "20231020120000id_": MW_OLD}
    store.transport = FakeTransport(routes)
    stats = {}
    consensus.ingest(conn, store, c, None, stats)
    q4 = conn.execute("SELECT count(*) FROM expectation_obs WHERE target_period='FY2023Q4'").fetchone()[0]
    assert q4 == 0
    assert stats.get("consensus_skipped_previous_quarter_unreported", 0) >= 1


def test_split_between_capture_and_release_rebases_eps(googl):
    conn, store, c, doc = googl
    conn.execute("""INSERT INTO corporate_action(company_id, action_type, ex_date, ratio, available_at,
        availability_basis, provider, source_doc_id, retrieved_at, natural_key) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (c["company_id"], "split", "2024-01-10", 20.0, "2024-01-10T14:30:00Z", "t", "yahoo_chart", doc,
                  "2026-01-01T00:00:00Z", "s1"))
    routes = {"cdx/search": lambda u: cdx([("20231220120000", "d1")]),
              "20231220120000id_": MW_NEW.replace("1.59", "31.80")}
    store.transport = FakeTransport(routes)
    consensus.ingest(conn, store, c, None, {})
    r = conn.execute("SELECT * FROM expectation_obs WHERE target_period='FY2023Q4' AND stat='mean'").fetchone()
    assert r["value"] == pytest.approx(31.80 / 20) and "split 20:1" in r["adjustments"]


def test_grade_c_or_d_is_never_point_in_time(env):
    conn, store, tmp = env
    cid = conn.execute("SELECT company_id FROM company WHERE ticker='MSFT'").fetchone()[0]
    put(conn, company_id=cid, cls="analyst_consensus", provider="x", metric="eps", stat="mean", value=1.0,
        as_of="2024-01-01T00:00:00Z", as_of_basis="t", available_at="2024-01-01T00:00:00Z", grade="C", method="t",
        natural_key="k1")
    assert conn.execute("SELECT available_at FROM expectation_obs").fetchone()[0] is None
    with pytest.raises(Exception, match="append-only"):
        conn.execute("UPDATE expectation_obs SET value=2")


def test_meta_outlook_parser_and_revisions():
    t = ("We expect fourth quarter 2022 total revenue to be in the range of $30-32.5 billion. "
         "We expect 2022 total expenses to be in the range of $85-87 billion, updated from our prior outlook of "
         "$85-88 billion. We anticipate our full-year 2023 capital expenditures will be in the range of $34-37 billion.")
    out = {(o["target"], o["metric"]): o for o in guidance.parse_meta_outlook(t)}
    assert out[("FY2022Q4", "revenue")]["low"] == 30e9 and out[("FY2022Q4", "revenue")]["high"] == 32.5e9
    assert out[("FY2022", "total_expenses")]["prior_low"] == 85e9
    assert out[("FY2023", "capex")]["high"] == 37e9


def test_microsoft_transcript_parser():
    t = ("Now to our outlook. Next, to segment guidance. In Productivity and Business Processes, we expect revenue "
         "of $19.3 to $19.6 billion or growth between 10% and 12%. For Intelligent Cloud we expect revenue of $26 to "
         "$26.3 billion. In Azure, we expect Q3 revenue growth to be 26% to 27% in constant currency. In More Personal "
         "Computing, we expect revenue of $14.7 to $15.1 billion US dollars.")
    out = {o["metric"]: o for o in guidance.parse_ms_transcript(t)}
    assert out["segment_revenue_pbp"]["low"] == 19.3e9 and out["segment_revenue_intelligent_cloud"]["high"] == 26.3e9
    assert out["azure_growth_cc"]["low"] == 26 and out["segment_revenue_mpc"]["low"] == 14.7e9


def test_implied_move_and_reverse_dcf_math():
    assert implied.implied_move(0.30, 0.35) is None                 # no event premium
    mv = implied.implied_move(0.45, 0.30)
    var = (0.45 ** 2 - 0.30 ** 2) * 30 / 365 + 0.30 ** 2 * 30 / 365 / 21
    assert mv == pytest.approx((2 / 3.141592653589793) ** 0.5 * var ** 0.5)
    target = implied.pv(100.0, 0.12, 0.09)
    assert implied.solve_growth(target, 100.0, 0.09) == pytest.approx(0.12, abs=1e-6)
    assert implied.solve_growth(target, -5.0, 0.09) is None          # negative FCF: no implied growth


def test_link_ignores_expectations_published_after_release(googl):
    conn, store, c, doc = googl
    for val, at in ((1.50, "2024-01-20T00:00:00Z"), (9.99, "2024-01-30T21:02:00Z")):   # second: 1 min after release
        put(conn, company_id=c["company_id"], cls="analyst_consensus", provider="P", metric="eps",
            target_period="FY2023Q4", stat="mean", value=val, as_of=at, as_of_basis="t", available_at=at, grade="B",
            method="t", natural_key=f"x{val}")
    ev = conn.execute("SELECT * FROM event WHERE accession_no='A-24-1'").fetchone()
    assert link.event_row(conn, ev, "2099-01-01T00:00:00Z", store)["consensus_eps"] == 1.50


def test_sec_contact_config(tmp_path):
    from iie.config import SEC_PLACEHOLDER, sec_user_agent
    assert sec_user_agent({}, {}) == (SEC_PLACEHOLDER, False)
    assert sec_user_agent({}, {"SEC_CONTACT_NAME": "Jane Doe", "SEC_CONTACT_EMAIL": "not-an-email"})[1] is False
    ua, ok = sec_user_agent({}, {"SEC_CONTACT_NAME": "Jane Doe", "SEC_CONTACT_EMAIL": "jane@example.com"})
    assert ok and ua.endswith("Jane Doe jane@example.com")
    assert sec_user_agent({"SEC_USER_AGENT": "X y@z.io"}, {})[0] == "X y@z.io"


def test_local_env_file_is_git_ignored():
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=root)
    assert r.returncode == 0


def test_forward_snapshot_maps_calendar_months_to_fiscal_quarters(env):
    conn, store, tmp = env
    from iie.expect import forward
    page = json.dumps({"data": {"quarterlyForecast": {"rows": [
        {"fiscalEnd": "Dec 2026", "consensusEPSForecast": 4.86, "highEPSForecast": 4.97, "lowEPSForecast": 4.81,
         "noOfEstimates": 12}]}, "yearlyForecast": {"rows": [{"fiscalEnd": "Jun 2027", "consensusEPSForecast": 19.89}]}}})
    store.transport = FakeTransport({"earnings-forecast": page})
    c = conn.execute("SELECT * FROM company WHERE ticker='MSFT'").fetchone()
    set_fiscal_year_end(conn, c["company_id"], "0630")
    c = conn.execute("SELECT * FROM company WHERE ticker='MSFT'").fetchone()
    forward.snapshot(conn, store, c, None, {})
    rows = {(r["target_period"], r["stat"]): r for r in conn.execute("SELECT * FROM expectation_obs")}
    assert rows[("FY2027Q2", "mean")]["value"] == 4.86          # Oct-Dec 2026 = Microsoft fiscal Q2 2027
    assert rows[("FY2027", "mean")]["value"] == 19.89
    assert rows[("FY2027Q2", "mean")]["available_at"] == rows[("FY2027Q2", "mean")]["as_of"]


def test_alphabet_capex_guidance_parser():
    t = ("we expect to invest approximately $75 billion in capital expenditures in 2025. Later: we now expect 2025 "
         "capital expenditures to be in a range of $91 billion to $93 billion. Our 2026 CapEx investments are "
         "anticipated to be in the range of $175 to $185 billion.")
    out = guidance.parse_alphabet_capex(t)
    assert {"target": "FY2025", "point": 75e9} == {k: out[0][k] for k in ("target", "point")}
    assert (out[1]["low"], out[1]["high"]) == (91e9, 93e9) and out[2]["target"] == "FY2026"


def test_null_valued_observations_are_not_duplicated(env):
    conn, store, tmp = env
    cid = conn.execute("SELECT company_id FROM company WHERE ticker='GOOGL'").fetchone()[0]
    for _ in range(2):
        put(conn, company_id=cid, cls="market_implied", provider="m", metric="x", stat="point", value=None,
            as_of="2024-01-01T00:00:00Z", as_of_basis="t", available_at="2024-01-01T00:00:00Z", grade="B",
            method="t", natural_key="nk-null")
    assert conn.execute("SELECT count(*) FROM expectation_obs").fetchone()[0] == 1


def test_numeric_observation_with_null_text_is_deduplicated(env):
    """Regression: a plain UNIQUE(natural_key, value, value_text) never fires when value_text IS NULL."""
    conn, store, tmp = env
    cid = conn.execute("SELECT company_id FROM company WHERE ticker='META'").fetchone()[0]
    for _ in range(3):
        put(conn, company_id=cid, cls="analyst_consensus", provider="p", metric="eps", stat="mean", value=2.5,
            as_of="2024-01-01T00:00:00Z", as_of_basis="t", available_at="2024-01-01T00:00:00Z", grade="B",
            method="t", natural_key="mw|META|FY2024Q1|mean|x")
    assert conn.execute("SELECT count(*) FROM expectation_obs").fetchone()[0] == 1


def test_meta_outlook_variants_with_implicit_year():
    t = ("We expect 2023 total expenses to be in the range of $89-95 billion. We recorded charges in the fourth quarter "
         "of 2022. We expect capital expenditures to be in "
         "the range of $30-33 billion, lowered from our prior estimate of $34-37 billion. For 2024, we expect capital "
         "expenditures to be in the range of $30-37 billion.")
    out = {(o["target"], o["metric"]): o for o in guidance.parse_meta_outlook(t)}
    assert out[("FY2023", "capex")]["low"] == 30e9 and out[("FY2023", "capex")]["prior_low"] == 34e9
    assert out[("FY2023", "capex")]["year_basis"].startswith("inferred")
    assert out[("FY2024", "capex")]["high"] == 37e9


def test_meta_2020_wording():
    t = ("As a result, we expect total expenses in 2020 to be between $52-56 billion, down from the prior range of "
         "$54-59 billion.")
    o = guidance.parse_meta_outlook(t)[0]
    assert (o["target"], o["low"], o["high"], o["prior_low"]) == ("FY2020", 52e9, 56e9, 54e9)
