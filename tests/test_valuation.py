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
