"""Unit tests: time, fiscal calendar, evidence store, immutability, parsers, price un-adjustment."""
import sqlite3
from datetime import date

import pytest

from conftest import RELEASE_HTML
from iie.derive import as_traded_close, split_factor_between
from iie.fiscal import fiscal_label, fiscal_year_of
from iie.ingest.documents import chunk_text, html_to_text, parse_index_headers
from iie.ingest.earnings_release import parse_release
from iie.timeutil import edgar_acceptance_to_utc, edgar_header_acceptance_to_utc, parse_cutoff


def test_cutoff_date_is_end_of_new_york_day():
    assert parse_cutoff("2024-01-30") == "2024-01-31T04:59:59Z"        # EST
    assert parse_cutoff("2024-07-01") == "2024-07-02T03:59:59Z"        # EDT
    assert parse_cutoff("2024-01-30T17:30:00-05:00") == "2024-01-30T22:30:00Z"
    assert parse_cutoff("2024-01-30T17:30:00") == "2024-01-30T22:30:00Z"  # naive = New York


def test_edgar_acceptance_formats_agree():
    # submissions API (UTC) and filing header (Eastern) describe the same instant
    assert edgar_acceptance_to_utc("2026-10-28T20:01:33.000Z") == edgar_header_acceptance_to_utc("20261028160133")


@pytest.mark.parametrize("start,end,fye,expected", [
    ("2023-10-01", "2023-12-31", "0630", (2024, "Q2")),   # Microsoft FY24 Q2
    ("2023-07-01", "2024-06-30", "0630", (2024, "FY")),
    ("2023-01-01", "2023-09-30", "1231", (2023, "YTD9")),
    ("2023-07-01", "2023-09-30", "1231", (2023, "Q3")),
    (None, "2023-12-31", "1231", (2023, "INSTANT")),
    (None, "2023-09-30", "0630", (2024, "INSTANT")),
])
def test_fiscal_labels(start, end, fye, expected):
    ps = date.fromisoformat(start) if start else None
    assert fiscal_label(ps, date.fromisoformat(end), fye) == expected


def test_fiscal_year_52_53_week_slack():
    assert fiscal_year_of(date(2024, 1, 2), "1231") == 2023


def test_evidence_roundtrip_and_corruption(env):
    conn, store, tmp = env
    f = store.register("https://example.test/a", b"hello", source_system="sec_edgar", doc_type="t")
    assert store.read(f.doc_id) == b"hello"
    # tamper with the stored bytes -> read must refuse
    import gzip
    p = store.root / conn.execute("SELECT storage_path FROM source_document WHERE doc_id=?", (f.doc_id,)).fetchone()[0]
    with gzip.open(p, "wb") as fh:
        fh.write(b"HELLO")
    with pytest.raises(Exception, match="corrupted"):
        store.read(f.doc_id)


def test_evidence_tables_are_append_only(env):
    conn, store, _ = env
    f = store.register("https://example.test/a", b"x", source_system="sec_edgar", doc_type="t")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE source_document SET url='y' WHERE doc_id=?", (f.doc_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM source_document WHERE doc_id=?", (f.doc_id,))
    for t in ("observation", "price_bar", "macro_observation", "filing", "event", "corporate_action"):
        trig = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (t,)).fetchone()
        assert trig[0] == 2, t


def test_release_parser_picks_current_year_column_and_scale():
    p = parse_release(RELEASE_HTML.encode())
    assert p["period_end"] == date(2023, 12, 31)
    assert p["scale_word"] == "millions"
    assert p["values"]["revenue"][0] == 130
    assert p["values"]["net_income"][0] == 30
    assert p["values"]["operating_income"][0] == 31
    assert p["values"]["eps_diluted"][0] == 1.64


def test_release_parser_current_year_first_layout_and_non_gaap_table_skipped():
    html = """<table><tr><td>($ in millions, except per share amounts)</td><td>As Reported (GAAP)</td></tr>
    <tr><td>Three Months Ended June 30,</td></tr><tr><td>2024</td><td>Percentage Change</td></tr>
    <tr><td>Revenue</td><td>$9</td></tr><tr><td>Net income</td><td>$1</td></tr></table>
    <table><tr><td>(In millions, except per share amounts)</td></tr><tr><td>Three Months Ended June 30,</td></tr>
    <tr><td>2024</td><td>2023</td></tr><tr><td>Total revenue</td><td>$64,727</td><td>$56,189</td></tr>
    <tr><td>Net income</td><td>$22,036</td><td>$20,081</td></tr><tr><td>Earnings per share:</td></tr>
    <tr><td>Basic</td><td>$2.96</td><td>$2.70</td></tr><tr><td>Diluted</td><td>$2.95</td><td>$2.69</td></tr>
    <tr><td>Weighted-average shares outstanding:</td></tr><tr><td>Diluted</td><td>7,469</td><td>7,474</td></tr>
    </table>"""
    p = parse_release(html.encode())
    assert p["period_end"] == date(2024, 6, 30)
    assert p["values"]["revenue"][0] == 64727
    assert p["values"]["eps_diluted"][0] == 2.95      # not the share-count 'Diluted' row


def test_index_headers_parsing():
    t = "&lt;ACCEPTANCE-DATETIME&gt;20240130160126\n&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;SEQUENCE&gt;2\n" \
        "&lt;FILENAME&gt;ex.htm\n"
    acc, docs = parse_index_headers(t)
    assert acc == "2024-01-30T21:01:26Z"
    assert docs == [("EX-99.1", "ex.htm")]


def test_text_extraction_and_chunk_offsets():
    text = html_to_text(b"<html><head><title>x</title></head><body><p>Item 7. MD&amp;A</p><p>" +
                        b"Revenue grew. " * 200 + b"</p></body></html>")
    chunks = chunk_text(text, target=500, overlap=50)
    assert len(chunks) > 3
    for s, e, sec, t in chunks:
        assert text[s:e] == t
    assert chunks[0][2].startswith("Item 7.")
    assert all(c[2] == "Item 7. MD&A" for c in chunks[1:])


def test_as_traded_close_undoes_vendor_split_adjustment():
    splits = [{"ex_date": "2022-07-18", "ratio": 20.0}]
    bar = {"trade_date": "2022-07-15", "close": 112.0, "retrieved_at": "2026-11-06T10:00:00Z"}
    px, factor, _ = as_traded_close(bar, splits)
    assert factor == 20 and px == 2240.0
    after = {"trade_date": "2022-07-18", "close": 113.0, "retrieved_at": "2026-11-06T10:00:00Z"}
    assert as_traded_close(after, splits)[0] == 113.0
    # a vendor file retrieved before the split carries no adjustment, so nothing is undone
    early = {"trade_date": "2022-07-15", "close": 2240.0, "retrieved_at": "2022-07-16T10:00:00Z"}
    assert as_traded_close(early, splits)[0] == 2240.0
    assert split_factor_between(splits, "2022-07-01", "2022-07-17") == 1.0


def test_rounding_classification():
    from iie.qa import classify_diff
    assert classify_diff(0.0, 3, 113_828e6, 113_828e6) == "pass"
    assert classify_diff(-1e6, 3, 113_828e6, 113_829e6) == "info"     # one unit of 'millions' rounding
    assert classify_diff(-5e6, 3, 113_824e6, 113_829e6) == "fail"
    assert classify_diff(1234.0, 3, 113_828_001_234.0, 113_828_000_000.0) == "fail"   # precise figures
