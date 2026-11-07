"""Writing and reading point-in-time expectation observations."""
from __future__ import annotations

import json

from ..timeutil import utcnow

CLASSES = ("reported_result", "management_guidance", "analyst_consensus", "market_implied", "narrative")


def put(conn, *, company_id, cls, provider, metric, stat, as_of, as_of_basis, grade, method, natural_key,
        value=None, value_text=None, unit=None, basis=None, target_period=None, available_at=None,
        source_doc_id=None, source_url=None, locator=None, adjustments=None, checks=None, notes=None, run_id=None):
    assert cls in CLASSES, cls
    if grade not in ("A", "B"):
        available_at = None                    # C/D never usable point-in-time
    cur = conn.execute(
        """INSERT OR IGNORE INTO expectation_obs(company_id, class, provider, metric, basis, target_period, stat, value,
             value_text, unit, as_of, as_of_basis, available_at, provenance_grade, source_doc_id, source_url, locator,
             method, adjustments, checks_json, notes, created_at, natural_key, run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, cls, provider, metric, basis, target_period, stat, value, value_text, unit, as_of, as_of_basis,
         available_at, grade, source_doc_id, source_url, locator, method, adjustments,
         json.dumps(checks, sort_keys=True, default=str) if checks else None, notes, utcnow(), natural_key, run_id))
    return cur.rowcount


def pit(conn, company_id, cls, metric, target_period, cutoff, knowledge_time, stat=None, provider=None):
    """Expectation observations usable at `cutoff`: available_at <= cutoff, recorded by knowledge_time,
    newest first."""
    sql = """SELECT * FROM expectation_obs WHERE company_id=? AND class=? AND metric=? AND target_period=?
             AND available_at IS NOT NULL AND available_at<=? AND created_at<=?"""
    p = [company_id, cls, metric, target_period, cutoff, knowledge_time]
    if stat:
        sql += " AND stat=?"
        p.append(stat)
    if provider:
        sql += " AND provider=?"
        p.append(provider)
    return conn.execute(sql + " ORDER BY as_of DESC, eo_id DESC", p).fetchall()
