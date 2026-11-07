"""Reverse DCF v2: model mathematics, input handling and the 'no figure when data is missing' rule."""
import pytest

from iie.expect import valuation as V


def inp(**kw):
    base = {"rf": 0.045, "erp": 0.045, "beta": {"raw": 1.2, "blume": 0.67 * 1.2 + 0.33}, "market_cap": 1000.0,
            "debt": 50.0, "finance_leases": 10.0, "cash_st": 100.0, "rd": 0.055, "tax_rate_used": 0.2,
            "revenue_ttm": 300.0, "ebit_ttm": 90.0, "sales_to_capital": 1.0, "breakeven": 0.022}
    base.update(kw)
    return base


def test_wacc_formula():
    w = V.wacc(inp())
    ke = 0.045 + (0.67 * 1.2 + 0.33) * 0.045
    e, d = 1000.0, 60.0
    assert w["ke"] == pytest.approx(ke)
    assert w["wacc"] == pytest.approx(e / (e + d) * ke + d / (e + d) * 0.055 * 0.8)


def test_growth_path_fades_linearly_to_terminal():
    p = V.growth_path(0.20, 0.03)
    assert p[:5] == [0.20] * 5 and p[-1] == pytest.approx(0.03)
    assert all(a >= b for a, b in zip(p, p[1:]))


def test_solver_recovers_known_growth_and_ev_is_monotonic():
    i = inp()
    w = V.wacc(i)["wacc"]
    gt = min(i["rf"], i["breakeven"] + V.REAL_GROWTH)
    ev = V.enterprise_value(300.0, 0.12, 0.3, 0.2, 1.0, w, gt, w)
    i["market_cap"] = ev - i["debt"] - i["finance_leases"] + i["cash_st"]
    # pass w explicitly: changing market_cap changes the capital-structure weights and hence the WACC
    assert V.implied_growth(i, w=w)["implied_revenue_cagr_y1_5"] == pytest.approx(0.12, abs=1e-6)
    vals = [V.enterprise_value(300.0, g, 0.3, 0.2, 1.0, w, gt, w) for g in (0.0, 0.1, 0.2)]
    assert vals[0] < vals[1] < vals[2]        # with RONIC = WACC and a positive margin, growth adds value here


def test_no_solution_returns_none_not_a_number():
    i = inp(market_cap=1e12)                  # price no growth rate in [-50%, 150%] can justify
    assert V.implied_growth(i)["implied_revenue_cagr_y1_5"] is None
    assert V.enterprise_value(300.0, 0.1, 0.3, 0.2, 1.0, 0.03, 0.035, 0.03) is None   # WACC <= g_T refused


def test_terminal_reinvestment_lowers_value():
    no_reinvest = V.enterprise_value(300.0, 0.1, 0.3, 0.2, 1.0, 0.09, 0.03, 1e9)
    with_reinvest = V.enterprise_value(300.0, 0.1, 0.3, 0.2, 1.0, 0.09, 0.03, 0.09)
    assert with_reinvest < no_reinvest


def test_bridge_requires_v1_inputs():
    assert V.bridge(inp(cfo_ttm=None, capex_ttm=1.0, sbc_ttm=1.0)) == []


def test_missing_inputs_are_reported_not_filled(env):
    conn, store, tmp = env
    c = conn.execute("SELECT * FROM company WHERE ticker='MSFT'").fetchone()
    out = V.inputs_at(conn, c, "2024-01-31T00:00:00Z", "2099-01-01T00:00:00Z")
    assert out["missing"] and "implied_revenue_cagr_y1_5" not in out


# ---------------------------------------------------------------- v2.1 patch: lineage, ERP, ROIC transition
def test_ttm_lineage_not_overwritten_by_three_years_ago(env, monkeypatch):
    """Regression: the 3-years-ago revenue TTM used to overwrite sources['ttm_revenue']."""
    conn, store, tmp = env

    class Idx:
        by_label, instants = {}, {}

        def __init__(self, *_):
            pass

        def get(self, *_):
            return None

    def fake_ttm(idx, metric, fy, q):
        return {"value": 1.0, "available_at": f"{fy}-11-01T00:00:00Z",
                "inputs": [{"id": fy * 10 + k, "fiscal_period": f"{fy}Q{k}"} for k in range(4)]}
    monkeypatch.setattr(V, "FiscalIndex", Idx)
    monkeypatch.setattr(V, "load_facts", lambda *a, **k: [])
    monkeypatch.setattr(V, "latest_quarter", lambda idx, m: (2026, 3))
    monkeypatch.setattr(V, "ttm", fake_ttm)
    c = conn.execute("SELECT * FROM company WHERE ticker='MSFT'").fetchone()
    out = V.inputs_at(conn, c, "2026-11-06T00:00:00Z", "2099-01-01T00:00:00Z")
    cur, old = out["sources"]["ttm_revenue"], out["sources"]["ttm_revenue_FY2023Q3"]
    assert cur["period"] == "TTM to FY2026Q3" and cur["inputs"] == [20260, 20261, 20262, 20263]
    assert cur["available_at"].startswith("2026") and old["available_at"].startswith("2023")
    assert old["inputs"] == [20230, 20231, 20232, 20233]


def test_ttm_lineage_matches_calculation_period_real_db():
    """On the real database: the observation IDs recorded for the current TTM revenue are the four fiscal quarters
    ending at the as-of quarter (or the YTD facts they derive from), their availability is the recorded one, and
    the 3-years-ago TTM is recorded separately."""
    import os
    from iie.db import connect, companies
    path = os.path.join(os.path.dirname(__file__), "..", "data", "iie.sqlite")
    if not os.path.exists(path):
        pytest.skip("real database not present")
    conn = connect(path)
    cut, kt = "2026-11-06T04:59:59Z", "2099-01-01T00:00:00Z"
    for c in companies(conn):
        out = V.inputs_at(conn, c, cut, kt)
        fy, q = int(out["as_of_quarter"][2:6]), int(out["as_of_quarter"][-1])
        src = out["sources"]["ttm_revenue"]
        assert src["period"] == f"TTM to FY{fy}Q{q}"
        rows = [conn.execute("SELECT * FROM observation WHERE obs_id=?", (i,)).fetchone() for i in src["inputs"]]
        assert all(r is not None for r in rows)
        # every input fact belongs to one of the four fiscal years spanned by the TTM window, none is after cutoff
        years = {fy, fy - 1}
        assert all(r["fiscal_year"] in years for r in rows), [(r["fiscal_year"], r["fiscal_period"]) for r in rows]
        assert max(r["available_at"] for r in rows) == src["available_at"] <= cut
        assert f"ttm_revenue_FY{fy - 3}Q{q}" in out["sources"]
        assert out["sources"][f"ttm_revenue_FY{fy - 3}Q{q}"]["inputs"] != src["inputs"]


def test_roic_fade_path_and_scenario_ordering():
    i = inp()
    w = V.wacc(i)["wacc"]
    m, tau, s2c = 0.3, 0.2, 1.0
    p = V.s2c_path(m, tau, s2c, w, "fade")
    roic = [x * m * (1 - tau) for x in p]
    assert roic[:5] == [pytest.approx(0.24)] * 5 and roic[-1] == pytest.approx(w)     # converges to RONIC by year 10
    assert V.s2c_path(m, tau, s2c, w, "abrupt") == [1.0] * 10
    # more reinvestment for the same growth under the fade -> lower EV -> higher implied growth
    gt = 0.03
    assert V.enterprise_value(300, 0.1, m, tau, s2c, w, gt, w, "fade") < V.enterprise_value(300, 0.1, m, tau, s2c, w, gt, w, "abrupt")
    sc = {s["scenario"][0]: s["implied_revenue_cagr_y1_5"] for s in V.roic_scenarios(i)}
    assert sc["B"] > sc["A"] > sc["D"] and sc["B"] > sc["C"] > sc["D"]


def test_monthly_erp_parser_handles_text_cells():
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Historical ERP"
    ws.append(["Start of month", "S&P 500", "ERP (T12m)", "ERP (Normalized)"])
    from datetime import datetime
    ws.append([datetime(2026, 9, 1), 7686, 0.0409, "6,36%"])
    ws.append([datetime(2026, 10, 1), 7652, "3,70%", 0.033])
    b = io.BytesIO()
    wb.save(b)
    assert V.parse_damodaran_monthly(b.getvalue()) == [("2026-09-01", 0.0409), ("2026-10-01", 0.037)]
