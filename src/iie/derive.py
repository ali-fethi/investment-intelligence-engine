"""Derived metrics. Pure functions over point-in-time facts; every output carries its methodology
and the ids of its inputs, and is only as available as its latest input."""
from __future__ import annotations

from datetime import date
from typing import Optional

from .config import METRICS
from .pit import Fact


class FiscalIndex:
    """Facts indexed by (metric, fiscal_year, fiscal_period); XBRL preferred over release tables."""

    def __init__(self, facts: dict):
        self.by_label: dict = {}
        self.instants: dict = {}
        for (metric, ps, pe), f in facts.items():
            if f.fiscal_period == "INSTANT":
                self.instants.setdefault(metric, {})[pe] = f
                continue
            k = (metric, f.fiscal_year, f.fiscal_period)
            cur = self.by_label.get(k)
            if cur is None or _better(f, cur):
                self.by_label[k] = f

    def get(self, metric, fy, fp) -> Optional[Fact]:
        return self.by_label.get((metric, fy, fp))


def _better(a: Fact, b: Fact) -> bool:
    ra, rb = a.source_kind == "xbrl_fact", b.source_kind == "xbrl_fact"
    if ra != rb:
        return ra
    return a.available_at > b.available_at


def derived(metric, value, unit, methodology, inputs, fy=None, fp=None, period_start=None, period_end=None):
    return {"metric": metric, "value": value, "unit": unit, "fiscal_year": fy, "fiscal_period": fp,
            "period_start": period_start, "period_end": period_end, "methodology": methodology,
            "inputs": inputs, "available_at": max(i["available_at"] for i in inputs) if inputs else None,
            "kind": "derived"}


def _ref(f: Fact, role: str) -> dict:
    return {"role": role, "table": "observation", "id": f.obs_id, "value": f.value, "concept": f.concept,
            "fiscal_period": f"{f.fiscal_year}{f.fiscal_period}", "available_at": f.available_at}


def quarter_value(idx: FiscalIndex, metric: str, fy: int, q: int):
    """Reported three-month value, or (additive metrics only) the difference of year-to-date values.

    Returns (item_dict, fact_or_None). item_dict is a reported item or a derived item."""
    f = idx.get(metric, fy, f"Q{q}")
    if f is not None and f.source_kind == "xbrl_fact":
        return dict(f.as_item(), kind="reported"), f
    # structured XBRL (even when derived from year-to-date values) supersedes an earnings-release table
    item = _derive_quarter(idx, metric, fy, q) if METRICS[metric]["additive"] else None
    if item is not None:
        if f is not None:
            item["supersedes"] = {"table": "observation", "id": f.obs_id, "source_kind": f.source_kind,
                                  "value": f.value}
        return item, None
    if f is not None:
        return dict(f.as_item(), kind="reported"), f
    return None, None


def _derive_quarter(idx: FiscalIndex, metric: str, fy: int, q: int):
    ytd_label = {1: None, 2: "YTD6", 3: "YTD9", 4: "FY"}[q]
    prev_label = {2: "Q1", 3: "YTD6", 4: "YTD9"}.get(q)
    if ytd_label is None:
        return None
    a = idx.get(metric, fy, ytd_label)
    b = idx.get(metric, fy, prev_label)
    if b is None and q == 4:
        # FY minus the three reported/derived quarters
        parts = [quarter_value(idx, metric, fy, i) for i in (1, 2, 3)]
        if a is None or any(p[0] is None for p in parts):
            return None
        inputs = [_ref(a, "FY")]
        for i, (item, _) in enumerate(parts, 1):
            inputs += item["inputs"] if item.get("kind") == "derived" else [
                {"role": f"Q{i}", "table": "observation", "id": item["evidence"]["id"], "value": item["value"],
                 "concept": item["concept"], "fiscal_period": f"{fy}Q{i}", "available_at": item["available_at"]}]
        v = a.value - sum(p[0]["value"] for p in parts)
        return derived(metric, v, a.unit, "Q4 = FY - (Q1 + Q2 + Q3)", inputs, fy, "Q4", None, a.period_end)
    if a is None or b is None:
        return None
    return derived(metric, a.value - b.value, a.unit, f"Q{q} = {ytd_label} - {prev_label} (year-to-date difference)",
                   [_ref(a, ytd_label), _ref(b, prev_label)], fy, f"Q{q}", b.period_end, a.period_end)


def latest_quarter(idx: FiscalIndex, metric: str = "revenue"):
    """Latest (fy, q) for which the metric is reported or derivable."""
    labels = sorted({(fy, fp) for (m, fy, fp) in idx.by_label if m == metric and fp in ("Q1", "Q2", "Q3", "Q4", "FY")},
                    reverse=True)
    for fy, fp in labels:
        q = 4 if fp == "FY" else int(fp[1])
        item, _ = quarter_value(idx, metric, fy, q)
        if item is not None:
            return fy, q
    return None


def prev_quarter(fy, q, n=1):
    for _ in range(n):
        fy, q = (fy - 1, 4) if q == 1 else (fy, q - 1)
    return fy, q


def ttm(idx: FiscalIndex, metric: str, fy: int, q: int):
    if not METRICS[metric]["additive"]:
        return None
    parts, inputs = [], []
    cfy, cq = fy, q
    for _ in range(4):
        item, _ = quarter_value(idx, metric, cfy, cq)
        if item is None:
            return None
        parts.append(item["value"])
        inputs += item["inputs"] if item.get("kind") == "derived" else [
            {"role": f"{cfy}Q{cq}", "table": "observation", "id": item["evidence"]["id"], "value": item["value"],
             "concept": item["concept"], "fiscal_period": f"{cfy}Q{cq}", "available_at": item["available_at"]}]
        cfy, cq = prev_quarter(cfy, cq)
    return derived(f"{metric}_ttm", sum(parts), METRICS[metric]["unit"],
                   f"TTM = sum of four fiscal quarters ending {fy}Q{q} (reported or YTD-derived)", inputs, fy, f"TTM_Q{q}")


def as_traded_close(bar, splits) -> tuple[float, float, str]:
    """Undo the vendor's split adjustment: close_as_traded = close_vendor * prod(ratio) over splits with
    trade_date < ex_date <= date(retrieved_at).  Uses post-cutoff splits ONLY to remove the vendor's own
    retroactive adjustment, which restores the price that actually printed on trade_date."""
    retrieved = bar["retrieved_at"][:10]
    factor = 1.0
    for s in splits:
        if bar["trade_date"] < s["ex_date"] <= retrieved:
            factor *= s["ratio"]
    return bar["close"] * factor, factor, (
        f"as-traded close = vendor split-adjusted close x {factor:g} (product of split ratios with ex-date after "
        f"{bar['trade_date']} and on/before retrieval date {retrieved})")


def split_factor_between(splits, d0: str, d1: str) -> float:
    """Product of split ratios with d0 < ex_date <= d1 (known by d1)."""
    f = 1.0
    for s in splits:
        if d0 < s["ex_date"] <= d1:
            f *= s["ratio"]
    return f


def rounding_unit(*values) -> float:
    """Largest power of 1000 (up to 1e6) that divides every value: the reporting precision."""
    for unit in (1e6, 1e3, 1.0):
        if all(abs(v / unit - round(v / unit)) < 1e-9 for v in values):
            return unit
    return 0.0


def classify_diff(diff, n_terms, *values):
    """pass = exact; info = within rounding of n_terms independently rounded figures; fail otherwise."""
    if abs(diff) < 1e-9 * max(1.0, *[abs(v) for v in values]):
        return "pass"
    if abs(diff) <= 0.5 * rounding_unit(*values) * n_terms + 1e-6:
        return "info"
    return "fail"


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
