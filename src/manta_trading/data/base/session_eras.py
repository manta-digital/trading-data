"""Dated regular hours for session extension — slice 301 D1, D3.

A calendar whose ``trading_calendars.hours_dated`` is true takes its regular
hours from ``trading_calendar_hours``, one row per era. Extension splits a
requested range at each era's ``effective_from`` and populates each segment
with that era's hours; eras are keyed by session date like the sessions, so no
session spans two segments. An era's ``sunday_open`` reaches
``populate_trading_sessions`` (unchanged) as the late open of each session
whose open falls on a Sunday.

Every other calendar, and every database that predates the ``hours_dated``
column (migrations 058/059 on a fresh database), is not dated and keeps the
single ``trading_calendars`` open and close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

from manta_trading.data.base.trading_calendar import MarketStatus

if TYPE_CHECKING:
    import psycopg

_MONDAY = 0


class NoHoursEraError(LookupError):
    """A dated calendar has no era in force on a requested session date."""

    def __init__(self, calendar_id: str, day: date, detail: str) -> None:
        super().__init__(
            f"{calendar_id}: no hours era for {day.isoformat()} ({detail})"
        )
        self.calendar_id = calendar_id
        self.day = day


@dataclass(frozen=True)
class Era:
    """One era's hours as extension reads them."""

    effective_from: date
    market_open: time
    market_close: time
    sunday_open: time


@dataclass(frozen=True)
class Segment:
    """A stretch of ``[start, end]`` populated with one era's hours."""

    start: date
    end: date
    era: Era


def is_dated(conn: psycopg.Connection[Any], calendar_id: str) -> bool:
    """Whether ``calendar_id`` takes its hours from ``trading_calendar_hours``.

    False without reading the flag when the ``hours_dated`` column does not
    exist yet: a database without it cannot have eras.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'trading_calendars' AND column_name = 'hours_dated'"
        )
        if cur.fetchone() is None:
            return False
        cur.execute(
            "SELECT hours_dated FROM trading_calendars WHERE calendar_id = %s",
            (calendar_id,),
        )
        row = cur.fetchone()
    return bool(row and row[0])


def read_eras(conn: psycopg.Connection[Any], calendar_id: str) -> list[Era]:
    """The calendar's eras in date order."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT effective_from, market_open, market_close, sunday_open "
            "FROM trading_calendar_hours WHERE calendar_id = %s "
            "ORDER BY effective_from",
            (calendar_id,),
        )
        return [Era(**row) for row in cur.fetchall()]


def plan_segments(
    calendar_id: str, start: date, end: date, eras: list[Era]
) -> list[Segment]:
    """Cut ``[start, end]`` at each era boundary (eras in date order).

    Raises:
        NoHoursEraError: no eras at all, or ``start`` precedes the first era.
    """
    if not eras:
        raise NoHoursEraError(calendar_id, start, "calendar is dated but has no eras")
    if start < eras[0].effective_from:
        raise NoHoursEraError(
            calendar_id,
            start,
            f"first era starts {eras[0].effective_from.isoformat()}",
        )
    segments: list[Segment] = []
    for index, era in enumerate(eras):
        era_end = (
            eras[index + 1].effective_from - timedelta(days=1)
            if index + 1 < len(eras)
            else end
        )
        seg_start, seg_end = max(start, era.effective_from), min(end, era_end)
        if seg_start <= seg_end:
            segments.append(Segment(seg_start, seg_end, era))
    return segments


def with_sunday_opens(
    holidays: list[dict[str, Any]], segment: Segment
) -> list[dict[str, Any]]:
    """``holidays`` with ``sunday_open`` as the late open of Sunday-opening sessions.

    A session opens on a Sunday when it is dated Monday and the era opens the
    evening before (open after close). Its exception row keeps its own late
    open if it has one; a closed day stays closed.
    """
    era = segment.era
    if era.sunday_open == era.market_open or era.market_open < era.market_close:
        return holidays
    by_date = {row["holiday_date"]: row for row in holidays}
    day = segment.start
    while day <= segment.end:
        if day.weekday() == _MONDAY:
            by_date[day] = _sunday_open_row(day, by_date.get(day), era.sunday_open)
        day += timedelta(days=1)
    return list(by_date.values())


def _sunday_open_row(
    day: date, row: dict[str, Any] | None, sunday_open: time
) -> dict[str, Any]:
    if row is None:
        return {
            "holiday_date": day,
            "market_status": MarketStatus.LATE_OPEN.value,
            "early_close_time": None,
            "late_open_time": sunday_open,
        }
    if row["market_status"] == MarketStatus.CLOSED or row["late_open_time"]:
        return row
    return {**row, "late_open_time": sunday_open}
