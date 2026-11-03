"""Migration 066 (slice 301 D6): CME calendar history back to the dataset start.

Six steps in the runner's transaction, so any failed check rolls everything
back:

1. refuse if either CME calendar already has a row before 2020-01-01;
2. read every CME session dated 2020-01-01 or later;
3. create ``trading_calendar_hours``, add ``trading_calendars.hours_dated``,
   flag both CME calendars, and insert the eras and pre-2020 exceptions with a
   plain ``INSERT`` (a conflict fails the migration);
4. refuse if a calendar's latest era differs from its ``trading_calendars``
   hours;
5. extend both calendars over ``[CME_DATASET_START, 2019-12-31]`` through the
   dated path of ``extend_calendar_sessions``;
6. read the 2020-onward sessions again and refuse on any difference.

Not idempotent by design: the runner records it and never re-runs it.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

from manta_trading.data.base.session_extension import extend_calendar_sessions
from manta_trading.market.schema.seed_cme_calendar import (
    CME_EQUITY_CALENDAR_ID,
    CmeCalendarSeed,
    cme_holidays_insert,
)
from manta_trading.market.schema.seed_cme_equity_history import (
    CME_EQUITY_HISTORY_EXCEPTIONS,
)
from manta_trading.market.schema.seed_cme_hours import (
    CME_DATASET_START,
    CME_EQUITY_HOURS,
    CME_METALS_HOURS,
    HoursEra,
)
from manta_trading.market.schema.seed_cme_metals_calendar import (
    CME_METALS_CALENDAR_ID,
)
from manta_trading.market.schema.seed_cme_metals_history import (
    CME_METALS_HISTORY_EXCEPTIONS,
)

UNTOUCHED_FROM = date(2020, 1, 1)
HISTORY_END = date(2019, 12, 31)

_HISTORY: dict[str, tuple[tuple[HoursEra, ...], tuple[dict[str, Any], ...]]] = {
    CME_EQUITY_CALENDAR_ID: (CME_EQUITY_HOURS, CME_EQUITY_HISTORY_EXCEPTIONS),
    CME_METALS_CALENDAR_ID: (CME_METALS_HOURS, CME_METALS_HISTORY_EXCEPTIONS),
}

_CREATE_SQL = """
    CREATE TABLE trading_calendar_hours (
        calendar_id     varchar(32) NOT NULL
            REFERENCES trading_calendars (calendar_id),
        effective_from  date NOT NULL,
        market_open     time NOT NULL,
        market_close    time NOT NULL,
        sunday_open     time NOT NULL,
        source          text NOT NULL,
        PRIMARY KEY (calendar_id, effective_from)
    );
    ALTER TABLE trading_calendars
        ADD COLUMN hours_dated boolean NOT NULL DEFAULT false;
"""

_ERA_INSERT_SQL = """
    INSERT INTO trading_calendar_hours
        (calendar_id, effective_from, market_open, market_close, sunday_open, source)
    VALUES (%(calendar_id)s, %(effective_from)s, %(market_open)s,
            %(market_close)s, %(sunday_open)s, %(source)s)
"""


class CalendarHistoryError(RuntimeError):
    """A precondition or proof of migration 066 failed; nothing is applied."""


def apply_cme_calendar_history(conn: psycopg.Connection[Any]) -> None:
    """Python callable for migration 066 (the six steps in the docstring)."""
    calendar_ids = list(_HISTORY)
    _refuse_existing_history(conn, calendar_ids)
    before = _sessions_from(conn, calendar_ids, UNTOUCHED_FROM)
    with conn.cursor() as cur:
        cur.execute(_CREATE_SQL)
        cur.execute(
            "UPDATE trading_calendars SET hours_dated = true "
            "WHERE calendar_id = ANY(%s)",
            (calendar_ids,),
        )
        for calendar_id, (eras, exceptions) in _HISTORY.items():
            cur.executemany(_ERA_INSERT_SQL, [vars(era) for era in eras])
            sql, rows = cme_holidays_insert(_history_seed(calendar_id, exceptions))
            cur.executemany(sql, rows)
    for calendar_id, (eras, _) in _HISTORY.items():
        _check_latest_era(conn, calendar_id, eras[-1])
    for calendar_id in calendar_ids:
        extend_calendar_sessions(
            conn, calendar_id, start=CME_DATASET_START, end=HISTORY_END
        )
    after = _sessions_from(conn, calendar_ids, UNTOUCHED_FROM)
    if after != before:
        changed = sorted(set(before) ^ set(after))[:5]
        raise CalendarHistoryError(
            f"sessions dated {UNTOUCHED_FROM} or later changed "
            f"({len(set(before) ^ set(after))} differing rows, first {changed})"
        )


def _history_seed(
    calendar_id: str, exceptions: tuple[dict[str, Any], ...]
) -> CmeCalendarSeed:
    """A seed carrying only the history exceptions, for the shared INSERT."""
    return CmeCalendarSeed(
        calendar_id=calendar_id,
        calendar={},
        seed_start=CME_DATASET_START,
        holidays_seeded_through=HISTORY_END,
        exceptions=exceptions,
    )


def _count(cur: psycopg.Cursor[Any]) -> int:
    """The single ``count(*)`` value of the statement just executed."""
    row = cur.fetchone()
    if row is None:
        raise CalendarHistoryError("count(*) returned no row")
    return int(row[0])


def _refuse_existing_history(
    conn: psycopg.Connection[Any], calendar_ids: list[str]
) -> None:
    params = (calendar_ids, UNTOUCHED_FROM)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM trading_holidays "
            "WHERE calendar_id = ANY(%s) AND holiday_date < %s",
            params,
        )
        counts = {"trading_holidays": _count(cur)}
        cur.execute(
            "SELECT count(*) FROM trading_sessions "
            "WHERE calendar_id = ANY(%s) AND session_date < %s",
            params,
        )
        counts["trading_sessions"] = _count(cur)
    if any(counts.values()):
        raise CalendarHistoryError(
            f"CME rows already exist before {UNTOUCHED_FROM}: "
            + ", ".join(f"{table} {count}" for table, count in counts.items())
        )


def _sessions_from(
    conn: psycopg.Connection[Any], calendar_ids: list[str], first: date
) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT calendar_id, session_date, session_open_utc, session_close_utc "
            "FROM trading_sessions WHERE calendar_id = ANY(%s) AND session_date >= %s "
            "ORDER BY calendar_id, session_date",
            (calendar_ids, first),
        )
        return list(cur.fetchall())


def _check_latest_era(
    conn: psycopg.Connection[Any], calendar_id: str, latest: HoursEra
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT market_open, market_close FROM trading_calendars "
            "WHERE calendar_id = %s",
            (calendar_id,),
        )
        row = cur.fetchone()
    era_hours = (latest.market_open, latest.market_close)
    if row is None or tuple(row) != era_hours or latest.sunday_open != row[0]:
        raise CalendarHistoryError(
            f"{calendar_id}: latest era {era_hours} (Sunday open "
            f"{latest.sunday_open}) differs from trading_calendars {row}"
        )
