"""US Treasury par yield curve, FRED daily rates and ALFRED vintages of revised macro series.

Bitemporal storage rule (`_insert_if_new`): a value is stored only if it differs from what the
database already says was known at that time.  A value that changes between two retrievals of a
non-vintaged source is a *revision detected at retrieval* and is made available only from the
retrieval timestamp — never back-dated.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

from ..config import ALFRED_REVISED_SERIES, FRED_DAILY_SERIES, FRED_LAG_BUSINESS_DAYS
from ..timeutil import et_to_utc_str, next_business_day

TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
                "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
ALFRED_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={sid}&vintage_date={vintage}&cosd={start}"


def _insert_if_new(conn, rec: dict) -> int:
    prev = conn.execute(
        """SELECT value, available_at FROM macro_observation WHERE series_id=? AND obs_date=? AND available_at<=?
           ORDER BY available_at DESC, mobs_id DESC LIMIT 1""",
        (rec["series_id"], rec["obs_date"], rec["available_at"])).fetchone()
    if prev is not None and _same(prev["value"], rec["value"]):
        return 0
    any_prev = conn.execute("SELECT value FROM macro_observation WHERE series_id=? AND obs_date=? "
                            "ORDER BY available_at DESC, mobs_id DESC LIMIT 1",
                            (rec["series_id"], rec["obs_date"])).fetchone()
    if rec.get("vintage_date") is None and any_prev is not None:
        if _same(any_prev["value"], rec["value"]):
            return 0
        # non-vintaged source changed its history: only knowable from now on
        rec = dict(rec, available_at=rec["retrieved_at"], availability_basis="revision_detected_at_retrieval",
                   availability_verified=1)
    conn.execute(
        """INSERT INTO macro_observation(series_id, obs_date, value, unit, definition, vintage_date, available_at,
             availability_basis, availability_verified, source_doc_id, source_url, retrieved_at, natural_key, run_id)
           VALUES (:series_id,:obs_date,:value,:unit,:definition,:vintage_date,:available_at,:availability_basis,
             :availability_verified,:source_doc_id,:source_url,:retrieved_at,:natural_key,:run_id)""", rec)
    return 1


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < 1e-12


def _num(s: str):
    s = (s or "").strip()
    if s in ("", "."):
        return None
    return float(s)


def ingest_treasury(conn, store, years, run_id, stats) -> int:
    n = 0
    for y in years:
        f = store.fetch(TREASURY_URL.format(year=y), source_system="us_treasury", doc_type="yield_curve_csv",
                        run_id=run_id, title=f"Daily Treasury Par Yield Curve Rates {y}")
        n += load_treasury_csv(conn, f.doc_id, f.url, f.body, f.retrieved_at, run_id)
    stats["treasury_obs_new"] = n
    return n


def load_treasury_csv(conn, doc_id, url, body, retrieved_at, run_id) -> int:
    n = 0
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    for row in reader:
        d = datetime.strptime(row["Date"], "%m/%d/%Y").date()
        for tenor, v in row.items():
            if tenor == "Date" or v in (None, ""):
                continue
            sid = f"UST:{tenor.strip()}"
            n += _insert_if_new(conn, dict(
                series_id=sid, obs_date=d.isoformat(), value=_num(v), unit="Percent (bond-equivalent par yield)",
                definition=f"U.S. Treasury daily par yield curve rate, {tenor.strip()} tenor (indicative, CMT)",
                vintage_date=None, available_at=et_to_utc_str(d, 23, 59, 59),
                availability_basis="assumed_same_day_publication_end_of_day_ET", availability_verified=0,
                source_doc_id=doc_id, source_url=url, retrieved_at=retrieved_at,
                natural_key=f"ust|{tenor.strip()}|{d.isoformat()}", run_id=run_id))
    return n


def ingest_fred_daily(conn, store, start, run_id, stats) -> int:
    n = 0
    for sid, (unit, desc) in FRED_DAILY_SERIES.items():
        f = store.fetch(FRED_URL.format(sid=sid, start=start), source_system="fred", doc_type="fred_csv",
                        run_id=run_id, title=f"FRED {sid} (latest vintage)")
        lag = FRED_LAG_BUSINESS_DAYS.get(sid, 1)
        for obs_date, val in _read_fred_csv(f.body):
            d = date.fromisoformat(obs_date)
            for _ in range(lag):
                d = next_business_day(d)
            n += _insert_if_new(conn, dict(
                series_id=f"FRED:{sid}", obs_date=obs_date, value=val, unit=unit, definition=desc, vintage_date=None,
                available_at=et_to_utc_str(d, 17, 0, 0),
                availability_basis=f"assumed_lag_{lag}_business_day(s)_17:00_ET (checked vs ALFRED vintages in QA)",
                availability_verified=0, source_doc_id=f.doc_id, source_url=f.url, retrieved_at=f.retrieved_at,
                natural_key=f"fred|{sid}|{obs_date}", run_id=run_id))
    stats["fred_daily_obs_new"] = n
    return n


def _read_fred_csv(body: bytes):
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    for r in rows[1:]:
        if len(r) >= 2:
            yield r[0], _num(r[1])


def month_end_vintages(start: str, end: str | None = None):
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end) if end else date.today()
    out = []
    y, m = d.year, d.month
    while True:
        nm = date(y + (m == 12), m % 12 + 1, 1)
        last = date.fromordinal(nm.toordinal() - 1)
        if last > stop:
            break
        out.append(last.isoformat())
        y, m = nm.year, nm.month
    return out


def ingest_alfred(conn, store, vintages, start, run_id, stats, series=None) -> int:
    """Revised series: one ALFRED vintage per requested date; each value is available from its vintage date."""
    n = 0
    series = series or ALFRED_REVISED_SERIES
    for sid, (unit, desc) in series.items():
        for v in sorted(set(vintages)):
            n += _fetch_alfred_vintage(conn, store, sid, unit, desc, v, start, run_id)
    stats["alfred_obs_new"] = stats.get("alfred_obs_new", 0) + n
    return n


def _fetch_alfred_vintage(conn, store, sid, unit, desc, v, start, run_id) -> int:
    if store.latest(ALFRED_URL.format(sid=sid, vintage=v, start=start)) is not None:
        return 0
    f = store.fetch(ALFRED_URL.format(sid=sid, vintage=v, start=start), source_system="alfred",
                    doc_type="alfred_vintage_csv", run_id=run_id, title=f"ALFRED {sid} vintage {v}")
    n = 0
    for obs_date, val in _read_fred_csv(f.body):
        if val is None:
            continue
        n += _insert_if_new(conn, dict(
            series_id=f"FRED:{sid}", obs_date=obs_date, value=val, unit=unit, definition=desc, vintage_date=v,
            available_at=et_to_utc_str(date.fromisoformat(v), 23, 59, 59),
            availability_basis="alfred_vintage_date_end_of_day_ET (sampled vintage: conservative upper bound)",
            availability_verified=1, source_doc_id=f.doc_id, source_url=f.url, retrieved_at=f.retrieved_at,
            natural_key=f"alfred|{sid}|{obs_date}|{v}", run_id=run_id))
    return n


def ingest_alfred_checks(conn, store, vintages, start, run_id, stats) -> int:
    """Exact vintages of daily series at snapshot cutoffs, used by QA to verify the lag assumption."""
    n = 0
    for sid, (unit, desc) in FRED_DAILY_SERIES.items():
        for v in vintages:
            url = ALFRED_URL.format(sid=sid, vintage=v, start=start)
            if store.latest(url) is None:
                store.fetch(url, source_system="alfred", doc_type="alfred_vintage_csv_check", run_id=run_id,
                            title=f"ALFRED {sid} vintage {v} (lag check)")
                n += 1
    stats["alfred_check_docs"] = n
    return n
