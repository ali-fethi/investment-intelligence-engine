"""Point-in-time (PIT) access layer.

Every query here takes two clocks:
  cutoff          market time — only rows with available_at <= cutoff are visible
  knowledge_time  system time — only rows with retrieved_at <= knowledge_time are visible
Together they make a snapshot reproducible: later ingests cannot change what an earlier
(cutoff, knowledge_time) pair returns.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .config import METRICS


@dataclass
class Fact:
    obs_id: int
    metric: str
    concept: str
    value: float
    unit: str
    period_start: Optional[str]
    period_end: str
    fiscal_year: int
    fiscal_period: str
    source_kind: str
    accession_no: Optional[str]
    source_doc_id: int
    source_url: str
    published_at: Optional[str]
    available_at: str
    availability_basis: str
    retrieved_at: str
    reliability: str
    accounting_definition: str
    filing_form: Optional[str]
    # audit trail (all computed from information visible at the cutoff)
    first_reported_value: Optional[float] = None
    first_reported_at: Optional[str] = None
    n_reports: int = 1
    concept_conflicts: list = field(default_factory=list)

    def as_item(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "value", "unit", "concept", "accounting_definition", "period_start", "period_end", "fiscal_year",
            "fiscal_period", "source_kind", "reliability", "accession_no", "filing_form", "source_url",
            "source_doc_id", "published_at", "available_at", "availability_basis", "retrieved_at")}
        d["evidence"] = {"table": "observation", "id": self.obs_id}
        d["revision_history_at_cutoff"] = {"n_reports": self.n_reports, "first_reported_value": self.first_reported_value,
                                           "first_reported_at": self.first_reported_at,
                                           "revised": self.first_reported_value is not None and
                                           abs((self.first_reported_value or 0) - self.value) > 1e-9}
        if self.concept_conflicts:
            d["definition_conflicts"] = self.concept_conflicts
        return d


def _concept_priority():
    pr = {}
    for mid, m in METRICS.items():
        for i, qc in enumerate(m["concepts"]):
            tax, c = qc.split(":")
            pr[(tax, c)] = (mid, i)
    return pr


def load_facts(conn, company_id: int, cutoff: str, knowledge_time: str) -> dict:
    """{(metric, period_start, period_end): Fact} — the value known at the cutoff for each period.

    Rules, in order:
      1. visibility: available_at <= cutoff AND retrieved_at <= knowledge_time
      2. structured XBRL beats the earnings-release table once XBRL is visible
      3. within a source kind, the highest-priority concept wins; lower-priority concepts that
         disagree are reported as definition conflicts
      4. within a concept, the most recently *available* report wins (restatements supersede);
         the first report is kept in the audit trail
    """
    prio = _concept_priority()
    concepts = [(t, c) for (t, c) in prio]
    q_marks = ",".join("(?,?)" for _ in concepts)
    params = [x for tc in concepts for x in tc]
    rows = conn.execute(
        f"""SELECT * FROM observation WHERE company_id=? AND available_at<=? AND retrieved_at<=?
            AND value IS NOT NULL AND ((taxonomy, concept) IN (VALUES {q_marks}) OR source_kind='earnings_release_table')
            ORDER BY available_at, obs_id""",
        [company_id, cutoff, knowledge_time] + params).fetchall()
    groups = defaultdict(list)
    for r in rows:
        if r["source_kind"] == "earnings_release_table":
            mid, p, rank = r["concept"], 0, 1
        else:
            mid, p = prio[(r["taxonomy"], r["concept"])]
            rank = 0
        if METRICS[mid]["unit"] != _unit_family(r["unit"]):
            continue
        groups[(mid, r["period_start"], r["period_end"])].append((rank, p, r))
    out = {}
    for key, cands in groups.items():
        best_rank = min(c[0] for c in cands)
        same_kind = [c for c in cands if c[0] == best_rank]
        best_p = min(c[1] for c in same_kind)
        chosen_concept = [c[2] for c in same_kind if c[1] == best_p]
        latest = chosen_concept[-1]  # ordered by available_at
        first = chosen_concept[0]
        f = _to_fact(key[0], latest)
        f.first_reported_value, f.first_reported_at = first["value"], first["available_at"]
        f.n_reports = len({r["accession_no"] for r in chosen_concept})
        # definition conflicts: other concepts mapped to the same metric with a different latest value
        others = {}
        for rank, p, r in same_kind:
            if p != best_p:
                others[r["concept"]] = r
        for concept, r in others.items():
            if abs(r["value"] - f.value) > max(1.0, abs(f.value) * 1e-6):
                f.concept_conflicts.append({"concept": f"{r['taxonomy']}:{concept}", "value": r["value"],
                                            "obs_id": r["obs_id"]})
        out[key] = f
    return out


def _unit_family(u: str) -> str:
    return {"USD": "USD", "USD/shares": "USD/shares", "shares": "shares"}.get(u, u)


def _to_fact(metric, r) -> Fact:
    return Fact(obs_id=r["obs_id"], metric=metric, concept=f"{r['taxonomy']}:{r['concept']}", value=r["value"],
                unit=r["unit"], period_start=r["period_start"], period_end=r["period_end"],
                fiscal_year=r["fiscal_year"], fiscal_period=r["fiscal_period"], source_kind=r["source_kind"],
                accession_no=r["accession_no"], source_doc_id=r["source_doc_id"], source_url=r["source_url"],
                published_at=r["published_at"], available_at=r["available_at"],
                availability_basis=r["availability_basis"], retrieved_at=r["retrieved_at"],
                reliability=r["reliability"], accounting_definition=r["accounting_definition"],
                filing_form=r["filing_form"])


def latest_bar(conn, company_id, cutoff, knowledge_time):
    return conn.execute(
        """SELECT * FROM price_bar WHERE company_id=? AND available_at<=? AND retrieved_at<=?
           AND provider='yahoo_chart' AND retrieved_at>=available_at ORDER BY trade_date DESC, retrieved_at DESC, bar_id DESC LIMIT 1""",
        (company_id, cutoff, knowledge_time)).fetchone()


def bar_on_or_before(conn, company_id, trade_date, knowledge_time):
    return conn.execute(
        """SELECT * FROM price_bar WHERE company_id=? AND trade_date<=? AND retrieved_at<=?
           AND provider='yahoo_chart' AND retrieved_at>=available_at ORDER BY trade_date DESC, retrieved_at DESC, bar_id DESC LIMIT 1""",
        (company_id, trade_date, knowledge_time)).fetchone()


def corporate_actions(conn, company_id, knowledge_time, action_type=None):
    sql = "SELECT * FROM corporate_action WHERE company_id=? AND retrieved_at<=? AND provider='yahoo_chart'"
    params = [company_id, knowledge_time]
    if action_type:
        sql += " AND action_type=?"
        params.append(action_type)
    return conn.execute(sql + " ORDER BY ex_date", params).fetchall()


def macro_value(conn, series_id, cutoff, knowledge_time):
    """Latest observation date visible at cutoff, with the value as known at cutoff."""
    return conn.execute(
        """SELECT * FROM macro_observation m WHERE series_id=? AND available_at<=? AND retrieved_at<=?
           AND value IS NOT NULL AND mobs_id = (
             SELECT mobs_id FROM macro_observation m2 WHERE m2.series_id=m.series_id AND m2.obs_date=m.obs_date
             AND m2.available_at<=? AND m2.retrieved_at<=? ORDER BY m2.available_at DESC, m2.mobs_id DESC LIMIT 1)
           ORDER BY obs_date DESC LIMIT 1""",
        (series_id, cutoff, knowledge_time, cutoff, knowledge_time)).fetchone()


def macro_first_value(conn, series_id, obs_date, knowledge_time):
    return conn.execute("""SELECT * FROM macro_observation WHERE series_id=? AND obs_date=? AND retrieved_at<=?
                           ORDER BY available_at, mobs_id LIMIT 1""", (series_id, obs_date, knowledge_time)).fetchone()
