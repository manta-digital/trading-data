"""Ingest check names and the failure reasons they write (slice 225, TD8).

:class:`IngestCheck` is the one spelling of each check. Every reason starts
with ``<check>:`` so status can group failures by check without parsing free
text, and carries the evidence TD8's table names. The reasons are stored on
the unit (``failure_reason``) and shown by ``status`` and the ingest report.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from manta_trading.data.base.session_index import Session


class IngestCheck(StrEnum):
    COUNTS = "counts"
    RESOLUTION = "resolution"
    SESSION_BOUNDARY = "session_boundary"
    OVERLAP = "overlap"
    SHAPE = "shape"
    DECODE = "decode"


def utc_of_ns(ts_ns: int) -> datetime:
    """An epoch-nanosecond instant as an aware UTC datetime (µs precision)."""
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, UTC)


def _span(first_open: datetime | None, last_close: datetime | None) -> str:
    if first_open is None or last_close is None:
        return "no sessions populated"
    return f"populated {first_open.isoformat()} → {last_close.isoformat()}"


def _session(session: Session | None, zone: ZoneInfo) -> str:
    if session is None:
        return "none"
    opened = session.open_utc.astimezone(zone).isoformat()
    closed = session.close_utc.astimezone(zone).isoformat()
    return f"{session.session_date} [{opened}, {closed}]"


def counts_reason(provider: int | None, decoded: int, stored: int) -> str:
    return (
        f"{IngestCheck.COUNTS}: provider {provider}, decoded {decoded}, stored {stored}"
    )


def resolution_reason(
    product: str, instrument_id: int, ts_event_ns: int, unresolved: int
) -> str:
    return (
        f"{IngestCheck.RESOLUTION}: {unresolved} records resolve to no {product}"
        f" definition; first instrument_id {instrument_id}"
        f" at {utc_of_ns(ts_event_ns).isoformat()}"
    )


def outside_span_reason(
    ts_event_ns: int,
    first_open: datetime | None,
    last_close: datetime | None,
    count: int,
) -> str:
    return (
        f"{IngestCheck.SESSION_BOUNDARY}: {count} records outside the populated"
        f" calendar range ({_span(first_open, last_close)}); first at"
        f" {utc_of_ns(ts_event_ns).isoformat()}"
    )


def no_session_reason(
    ts_event_ns: int,
    zone: ZoneInfo,
    before: Session | None,
    after: Session | None,
    count: int,
) -> str:
    """Records in a break or on a closed day, with the sessions around the first."""
    first = utc_of_ns(ts_event_ns)
    return (
        f"{IngestCheck.SESSION_BOUNDARY}: {count} records in no session; first at"
        f" {first.isoformat()} ({first.astimezone(zone).isoformat()} {zone.key}),"
        f" between session {_session(before, zone)}"
        f" and session {_session(after, zone)}"
    )


def planning_span_reason(
    day: date, first_open: datetime | None, last_close: datetime | None
) -> str:
    """The unit's day is past the populated calendar (rows deleted; TD7)."""
    return (
        f"{IngestCheck.SESSION_BOUNDARY}: day {day} is outside the populated"
        f" calendar range ({_span(first_open, last_close)});"
        " run mt data extend, then reset"
    )


def overlap_reason(key: str | None, day: date, other_units: Sequence[int]) -> str:
    """A ``UniqueViolation`` on ``COPY``: another current unit holds the key."""
    return (
        f"{IngestCheck.OVERLAP}: key {key or 'unknown'} is already stored;"
        f" other current units on {day}: {list(other_units)}"
    )


def shape_reason(stype_in: str) -> str:
    return (
        f"{IngestCheck.SHAPE}: stype_in {stype_in}; the ledger's instrument set"
        " is defined for parent symbology only"
    )


def decode_reason(path: Path, error: BaseException) -> str:
    return f"{IngestCheck.DECODE}: {path}: {type(error).__name__}: {error}"
