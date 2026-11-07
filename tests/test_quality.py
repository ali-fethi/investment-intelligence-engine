"""Comparability classification and statistics of the consensus/EPS quality report."""
import pytest

from iie.expect import quality as Q
from iie.expect.store import put


def _row(q, cons, gaap, car, prov="FactSet via MarketWatch (archived)", age=5):
    return {"ticker": "MSFT", "quarter": q, "public_at": "2024-01-30T21:00:00Z", "reaction_day": "2024-01-31",
            "consensus_eps": cons, "consensus_eps_provider": prov, "consensus_eps_days_before": age,
            "eps_gaap": gaap, "car_d0": car, "scar_d0": 0.0, "attribution": "low"}


def test_classification_uses_street_actual_and_flags_gaap_differences(env):
    conn, store, tmp = env
    cid = conn.execute("SELECT company_id FROM company WHERE ticker='MSFT'").fetchone()[0]
    for q, v in (("FY2024Q1", 2.99), ("FY2024Q2", 2.93)):
        put(conn, company_id=cid, cls="reported_result", provider="FactSet", metric="eps", stat="street_actual",
            target_period=q, value=v, as_of="2024-03-01T00:00:00Z", as_of_basis="t", grade="C", method="t",
            natural_key=f"s{q}")
    rows = [_row("FY2024Q1", 2.65, 2.99, 0.01), _row("FY2024Q2", 2.78, 2.90, -0.01, prov="Zacks", age=45),
            _row("FY2024Q3", 2.80, 2.94, 0.02), _row("FY2024Q4", None, 2.95, 0.0)]
    c = {r["quarter"]: r for r in Q.classify(conn, rows)}
    assert c["FY2024Q1"]["comparability"] == "S" and c["FY2024Q1"]["comparability_gaap"] == "G="
    assert c["FY2024Q1"]["surprise"] == pytest.approx(2.99 / 2.65 - 1)
    assert c["FY2024Q2"]["comparability_gaap"] == "X"                  # GAAP 2.90 vs street 2.93
    assert any("mixed provider" in i for i in c["FY2024Q2"]["issues"])
    assert any("stale" in i for i in c["FY2024Q2"]["issues"])
    assert c["FY2024Q3"]["comparability"] == "G?"                       # no street actual: basis unverified
    assert c["FY2024Q4"]["comparability"] == "-"


def test_statistics_are_seeded_and_sane():
    pairs = [(i / 10, i / 10 + (0.05 if i % 2 else -0.05)) for i in range(-10, 11)]
    s1, s2 = Q.stats_for(pairs), Q.stats_for(pairs)
    assert s1 == s2                                    # reproducible (fixed seed)
    assert s1["spearman"] > 0.9 and s1["perm_p_two_sided"] < 0.01
    assert s1["boot_ci95"][0] <= s1["spearman"] <= s1["boot_ci95"][1]
    assert Q.stats_for(pairs[:5])["note"] == "too few observations"


def test_zero_day_old_consensus_counts_as_fresh():
    assert Q._age({"consensus_eps_days_before": 0}) == 0
    assert Q._age({"consensus_eps_days_before": None}) > 150
