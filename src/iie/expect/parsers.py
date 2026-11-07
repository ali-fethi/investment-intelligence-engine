"""Parsers for archived consensus pages. Each returns plain dicts and never guesses a missing field."""
from __future__ import annotations

import html as htmllib
import re

QLABEL = re.compile(r"Q([1-4])\s+(\d{4})")


def _text(body: bytes) -> str:
    t = body.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t)


def _num(s):
    s = s.replace(",", "").replace("$", "").strip()
    if s in ("", "N/A", "-", "--"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_marketwatch(body: bytes) -> dict | None:
    """MarketWatch 'analystestimates' (FactSet consensus). Returns
    {'next_report': {'quarter': 'Q2 2024', 'date': '01/31/2024'} | None,
     'forward': {'Q2 2024': {'high':..,'low':..,'average':..}, ...},        # consensus for unreported quarters
     'history': {'Q1 2024': {'estimate':..,'actual':..,'surprise':..}, ...}} # consensus at report, recorded later
    Fiscal-quarter labels are the company's own (e.g. Microsoft Q2 2024 = Oct-Dec 2023)."""
    t = _text(body)
    i = t.find("Quarterly Numbers")
    if i < 0:
        return None
    seg = t[i:i + 1500]
    out = {"next_report": None, "forward": {}, "history": {}}
    m = re.search(r"will report (Q[1-4] \d{4}) earnings on (\d{2}/\d{2}/\d{4})", seg)
    if m:
        out["next_report"] = {"quarter": m.group(1), "date": m.group(2)}
    # history block: labels then Estimate/Actual/Surprise rows
    h = re.search(r"((?:Q[1-4] \d{4} ){2,4})Estimate ((?:[-\d.,()]+ ){2,4})Actual ((?:[-\d.,()]+ ){2,4})", seg)
    if h:
        labels = QLABEL.findall(h.group(1))
        est, act = h.group(2).split(), h.group(3).split()
        for (q, y), e, a in zip(labels, est, act):
            out["history"][f"Q{q} {y}"] = {"estimate": _num(e), "actual": _num(a)}
    f = re.search(r"((?:Q[1-4] \d{4} ){1,3})High ((?:[-\d.,()]+ ){1,3})Low ((?:[-\d.,()]+ ){1,3})Average "
                  r"((?:[-\d.,()]+ ?){1,3})", seg)
    if f:
        labels = QLABEL.findall(f.group(1))
        hi, lo, av = f.group(2).split(), f.group(3).split(), f.group(4).split()
        for (q, y), a, b, c in zip(labels, hi, lo, av):
            out["forward"][f"Q{q} {y}"] = {"high": _num(a), "low": _num(b), "average": _num(c)}
    n = re.search(r"Number Of Ratings (\d+)", t)
    out["n_ratings"] = int(n.group(1)) if n else None
    return out if (out["forward"] or out["history"]) else None


def parse_marketwatch_old(body: bytes) -> dict | None:
    """2016-2019 MarketWatch layout: no quarter labels, only 'This Quarter / Next Quarter / This Fiscal /
    Next Fiscal'. The caller assigns the quarter point-in-time and validates it with `last_quarter_eps`."""
    t = _text(body)
    m = re.search(r"Earnings Per Share This Quarter Next Quarter This Fiscal Next Fiscal # of Estimates "
                  r"([\d.,]+) ([\d.,]+) ([\d.,]+) ([\d.,]+) Mean Estimate ([-\d.,()]+) ([-\d.,()]+) ([-\d.,()]+) "
                  r"([-\d.,()]+) High Estimates ([-\d.,()]+) ([-\d.,()]+) ([-\d.,()]+) ([-\d.,()]+) Low Estimates "
                  r"([-\d.,()]+) ([-\d.,()]+) ([-\d.,()]+) ([-\d.,()]+)", t)
    if not m:
        return None
    g = [_num(x) for x in m.groups()]
    cols = ("this_quarter", "next_quarter", "this_fiscal", "next_fiscal")
    out = {c: {"n": g[i], "mean": g[4 + i], "high": g[8 + i], "low": g[12 + i]} for i, c in enumerate(cols)}
    lq = re.search(r"Last Quarter's Earnings: ([-\d.,()]+)", t)
    fy = re.search(r"FY Report Date: (\d{1,2}) / (\d{4})", t)
    out["last_quarter_eps"] = _num(lq.group(1)) if lq else None
    out["fy_report"] = (int(fy.group(1)), int(fy.group(2))) if fy else None
    return out
