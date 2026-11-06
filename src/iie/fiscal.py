"""Fiscal-calendar mapping: from the period a fact describes to a fiscal label.

The fy/fp fields in SEC companyfacts belong to the *filing*, not to the fact (a Q2 10-Q
carries prior-year comparatives with the same fy/fp), so labels are recomputed here from the
period dates and the company's fiscal year end.
"""
from __future__ import annotations

from datetime import date


def fiscal_year_of(period_end: date, fye_mmdd: str) -> int:
    """Fiscal year is named after the calendar year in which it ends. A period ending up to 7 days
    after a fiscal year end (52/53-week calendars) still belongs to that fiscal year."""
    m, d = int(fye_mmdd[:2]), int(fye_mmdd[2:])
    for y in (period_end.year - 1, period_end.year, period_end.year + 1):
        fye = date(y, m, min(d, 28) if m == 2 else d)
        if (period_end - fye).days <= 7:
            return y
    return period_end.year + 1


def months_into_fiscal_year(period_end: date, fye_mmdd: str) -> int:
    fye_month = int(fye_mmdd[:2])
    m = period_end.month
    if period_end.day <= 7:  # 52/53-week calendars ending in the first days of a month
        m = 12 if m == 1 else m - 1
    return (m - fye_month - 1) % 12 + 1


def classify_duration(days: int | None) -> str | None:
    if days is None:
        return None
    if 80 <= days <= 100:
        return "Q"
    if 170 <= days <= 190:
        return "YTD6"
    if 260 <= days <= 285:
        return "YTD9"
    if 350 <= days <= 380:
        return "FY"
    return "OTHER"


def fiscal_label(period_start: date | None, period_end: date, fye_mmdd: str) -> tuple[int, str]:
    fy = fiscal_year_of(period_end, fye_mmdd)
    if period_start is None:
        return fy, "INSTANT"
    kind = classify_duration((period_end - period_start).days)
    if kind == "Q":
        q = (months_into_fiscal_year(period_end, fye_mmdd) + 2) // 3
        return fy, f"Q{q}"
    return fy, kind or "OTHER"
