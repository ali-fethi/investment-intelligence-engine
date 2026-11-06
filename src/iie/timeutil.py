"""Time helpers. All persisted timestamps are UTC ISO strings ending in 'Z'."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def utcnow() -> str:
    return to_utc_str(datetime.now(UTC))


def to_utc_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime refused; attach a timezone")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {s}")
    return dt.astimezone(UTC)


def et_to_utc_str(d: date, hh: int, mm: int = 0, ss: int = 0) -> str:
    return to_utc_str(datetime.combine(d, time(hh, mm, ss), tzinfo=ET))


def parse_cutoff(s: str) -> str:
    """A bare date means end of that day in New York (23:59:59 ET). Otherwise an aware ISO timestamp."""
    s = s.strip()
    if len(s) == 10:
        return et_to_utc_str(date.fromisoformat(s), 23, 59, 59)
    return to_utc_str(parse_utc(s) if (s.endswith("Z") or "+" in s[10:] or "-" in s[10:]) else _naive_et(s))


def _naive_et(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=ET)


def next_business_day(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def edgar_acceptance_to_utc(s: str) -> str:
    """submissions API gives e.g. '2026-10-28T20:01:33.000Z' (UTC; verified against the
    filing header ACCEPTANCE-DATETIME which is Eastern time)."""
    return to_utc_str(parse_utc(s.replace(".000Z", "Z")))


def edgar_header_acceptance_to_utc(s: str) -> str:
    """Filing header format 'YYYYMMDDHHMMSS' in Eastern time."""
    dt = datetime.strptime(s.strip(), "%Y%m%d%H%M%S").replace(tzinfo=ET)
    return to_utc_str(dt)
