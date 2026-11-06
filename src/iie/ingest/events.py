"""Structured event calendar built only from sources with verifiable timestamps.

Included: EDGAR filings (earnings releases = 8-K Item 2.02, periodic reports, other 8-K items),
split and dividend ex-dates from the price provider.
Not included (documented gap): forward-looking scheduled earnings dates — no free source
publishes them with a verifiable first-announcement timestamp.
"""
from __future__ import annotations

import json

ITEM_LABELS = {
    "1.01": "Entry into a material definitive agreement", "1.02": "Termination of a material agreement",
    "2.01": "Completion of acquisition or disposition", "2.02": "Results of operations and financial condition",
    "2.03": "Creation of a direct financial obligation", "2.05": "Costs associated with exit or disposal",
    "2.06": "Material impairments", "3.03": "Material modification of rights of security holders",
    "5.02": "Departure/appointment of directors or officers", "5.03": "Amendments to articles or bylaws",
    "5.07": "Submission of matters to a vote of security holders", "7.01": "Regulation FD disclosure",
    "8.01": "Other events", "9.01": "Financial statements and exhibits",
}


def build_events(conn, company, since, run_id, stats) -> int:
    cid = company["company_id"]
    rows = conn.execute("""SELECT * FROM filing WHERE company_id=? AND filing_date>=?
                           AND form IN ('8-K','8-K/A','10-K','10-Q','10-K/A','10-Q/A','DEF 14A')""",
                        (cid, since)).fetchall()
    n = 0
    for f in rows:
        items = [i.strip() for i in (f["items"] or "").split(",") if i.strip()]
        if f["form"].startswith("8-K"):
            etype = "earnings_release" if "2.02" in items else "material_event_8k"
        elif f["form"] == "DEF 14A":
            etype = "proxy_statement"
        else:
            etype = "periodic_report"
        details = {"form": f["form"], "items": {i: ITEM_LABELS.get(i, "") for i in items},
                   "report_date": f["report_date"], "filing_date": f["filing_date"], "url": f["primary_doc_url"]}
        cur = conn.execute(
            """INSERT OR IGNORE INTO event(company_id, event_type, event_date, available_at, availability_basis,
                 accession_no, details_json, source_doc_id, natural_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cid, etype, f["report_date"] or f["filing_date"], f["acceptance_datetime"], "sec_acceptance_datetime",
             f["accession_no"], json.dumps(details, sort_keys=True), f["source_doc_id"],
             f"filing|{f['accession_no']}", run_id))
        n += cur.rowcount
    for ca in conn.execute("SELECT * FROM corporate_action WHERE company_id=? AND ex_date>=?", (cid, since)):
        etype = "split_ex_date" if ca["action_type"] == "split" else "dividend_ex_date"
        details = {"ratio": ca["ratio"], "amount": ca["amount"], "amount_basis": ca["amount_basis"],
                   "provider": ca["provider"], "announced_at": ca["announced_at"]}
        cur = conn.execute(
            """INSERT OR IGNORE INTO event(company_id, event_type, event_date, available_at, availability_basis,
                 accession_no, details_json, source_doc_id, natural_key, run_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cid, etype, ca["ex_date"], ca["available_at"], ca["availability_basis"], None,
             json.dumps(details, sort_keys=True), ca["source_doc_id"], f"ca|{ca['action_id']}", run_id))
        n += cur.rowcount
    stats[f"events_new_{company['ticker']}"] = n
    return n
