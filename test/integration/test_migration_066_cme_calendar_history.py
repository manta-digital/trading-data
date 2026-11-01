"""Integration tests for migration 066, the CME calendar history (301 D6).

Every test runs on a throwaway database the conftest fixtures created. The
read-only checks share one module-scoped migrated database; the populated and
abort paths build their own.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import psycopg
import pytest
from psycopg_pool import ConnectionPool

import manta_trading.market.schema.migrations.cme_calendar_history as m066
from manta_trading.data.base.session_eras import is_dated
from manta_trading.data.base.session_extension import extend_calendar_sessions
from manta_trading.data.base.session_index import Session, SessionIndex
from manta_trading.data.tick.ingest_records import (
    SessionFrame,
    UnitCheckFailed,
    locate_sessions,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations
from manta_trading.market.schema.seed_cme_hours import (
    CME_DATASET_START,
    CME_HOURS_ERAS,
    HoursEra,
)

_CT = ZoneInfo("America/Chicago")
_CME = ("CME_EQUITY", "CME_METALS")
_MIGRATION_ID = "066_cme_calendar_history"
_UNTOUCHED_FROM = date(2020, 1, 1)


def _chain_through(migration_id: str) -> list[dict[str, Any]]:
    ids = [m["id"] for m in MINUTE_MIGRATIONS]
    return MINUTE_MIGRATIONS[: ids.index(migration_id) + 1]


def _before_066() -> list[dict[str, Any]]:
    ids = [m["id"] for m in MINUTE_MIGRATIONS]
    return MINUTE_MIGRATIONS[: ids.index(_MIGRATION_ID)]


@pytest.fixture
def conn(module_migrated_db: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(module_migrated_db) as connection:
        yield connection


def _sessions(
    conn: psycopg.Connection[Any], calendar_id: str, first: date, last: date
) -> list[Session]:
    rows = conn.execute(
        "SELECT session_date, session_open_utc, session_close_utc "
        "FROM trading_sessions WHERE calendar_id = %s "
        "AND session_date BETWEEN %s AND %s ORDER BY session_date",
        (calendar_id, first, last),
    ).fetchall()
    return [Session(calendar_id, d, o, c) for d, o, c in rows]


# ---------------------------------------------------------------------------
# Fresh database, full chain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("calendar_id", _CME)
def test_the_first_session_is_the_first_on_or_after_the_dataset_start(
    conn: psycopg.Connection[Any], calendar_id: str
) -> None:
    first = conn.execute(
        "SELECT min(session_date) FROM trading_sessions WHERE calendar_id = %s",
        (calendar_id,),
    ).fetchone()
    assert first == (CME_DATASET_START + timedelta(days=1),)


def test_eras_are_stored_with_their_sources(conn: psycopg.Connection[Any]) -> None:
    rows = conn.execute(
        "SELECT calendar_id, effective_from, market_open, market_close, "
        "sunday_open, source FROM trading_calendar_hours "
        "ORDER BY calendar_id, effective_from"
    ).fetchall()
    expected = sorted(
        (e.calendar_id, e.effective_from, e.market_open, e.market_close,
         e.sunday_open, e.source)
        for e in CME_HOURS_ERAS
    )  # fmt: skip
    assert rows == expected


def test_only_the_cme_calendars_are_dated(conn: psycopg.Connection[Any]) -> None:
    flags = dict(conn.execute("SELECT calendar_id, hours_dated FROM trading_calendars"))
    assert flags == {"CME_EQUITY": True, "CME_METALS": True, "NASDAQ": False,
                     "NYSE": False}  # fmt: skip
    assert [is_dated(conn, c) for c in ("CME_EQUITY", "CME_METALS")] == [True, True]
    assert [is_dated(conn, c) for c in ("NYSE", "NASDAQ")] == [False, False]


@pytest.mark.parametrize("calendar_id", _CME)
def test_pre_2020_sessions_skip_only_weekends_and_closures(
    conn: psycopg.Connection[Any], calendar_id: str
) -> None:
    dates = {
        s.session_date
        for s in _sessions(conn, calendar_id, CME_DATASET_START, date(2019, 12, 31))
    }
    closed = {
        row[0]
        for row in conn.execute(
            "SELECT holiday_date FROM trading_holidays WHERE calendar_id = %s "
            "AND market_status = 'closed'",
            (calendar_id,),
        )
    }
    day, missing = CME_DATASET_START, []
    while day < _UNTOUCHED_FROM:
        if day.weekday() < 5 and day not in closed and day not in dates:
            missing.append(day)
        day += timedelta(days=1)
    assert missing == []


@pytest.mark.parametrize("calendar_id", _CME)
def test_forward_extension_writes_what_it_wrote_before(
    conn: psycopg.Connection[Any], calendar_id: str
) -> None:
    last = conn.execute(
        "SELECT max(session_date) FROM trading_sessions WHERE calendar_id = %s",
        (calendar_id,),
    ).fetchone()[0]
    before = _sessions(conn, calendar_id, _UNTOUCHED_FROM, last)
    extend_calendar_sessions(conn, calendar_id, start=_UNTOUCHED_FROM, end=last)
    after = _sessions(conn, calendar_id, _UNTOUCHED_FROM, last)
    conn.rollback()
    assert after == before


def _ns(day: date, hour: int, minute: int = 0) -> int:
    instant = datetime.combine(day, time(hour, minute), tzinfo=_CT)
    return int(instant.timestamp()) * 1_000_000_000


def _frame(
    conn: psycopg.Connection[Any], calendar_id: str, around: date
) -> SessionFrame:
    sessions = _sessions(
        conn, calendar_id, around - timedelta(days=10), around + timedelta(days=10)
    )
    first_open, last_close = conn.execute(
        "SELECT min(session_open_utc), max(session_close_utc) FROM trading_sessions "
        "WHERE calendar_id = %s",
        (calendar_id,),
    ).fetchone()
    return SessionFrame(SessionIndex(sessions), first_open, last_close, _CT)


def _era_change_days() -> list[Any]:
    """The weekday after each era change and the weekday before it."""
    params = []
    for era in CME_HOURS_ERAS:
        if era.effective_from <= CME_DATASET_START:
            continue
        params.append(
            pytest.param(
                era,
                era.effective_from + timedelta(days=1),
                id=f"{era.calendar_id}-{era.effective_from}-after",
            )
        )
        params.append(
            pytest.param(
                _era_before(era),
                era.effective_from - timedelta(days=4),
                id=f"{era.calendar_id}-{era.effective_from}-before",
            )
        )
    return params  # fmt: skip


def _era_before(era: HoursEra) -> HoursEra:
    same = [e for e in CME_HOURS_ERAS if e.calendar_id == era.calendar_id]
    return same[same.index(era) - 1]


@pytest.mark.parametrize(("era", "day"), _era_change_days())
def test_instants_place_by_the_era_in_force(
    conn: psycopg.Connection[Any], era: HoursEra, day: date
) -> None:
    """An instant inside the era's hours places; one in its daily break fails."""
    assert day.weekday() in (1, 2, 3, 4), "pick a mid-week day"
    frame = _frame(conn, era.calendar_id, day)
    close = era.market_close
    just_before_close = _ns(day, close.hour, close.minute) - 60_000_000_000
    positions = locate_sessions(frame, np.array([just_before_close], dtype=np.int64))
    assert frame.index.sessions[int(positions[0])].session_date == day
    break_minute = _ns(day, close.hour, close.minute) + 60_000_000_000
    with pytest.raises(UnitCheckFailed, match="session_boundary"):
        locate_sessions(frame, np.array([break_minute], dtype=np.int64))


# ---------------------------------------------------------------------------
# Populated database and aborts
# ---------------------------------------------------------------------------


def _migrate(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, migrations)


def _dump(url: str, first: date) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as connection:
        return connection.execute(
            "SELECT calendar_id, session_date, session_open_utc, session_close_utc "
            "FROM trading_sessions WHERE session_date >= %s ORDER BY 1, 2",
            (first,),
        ).fetchall()


def test_a_populated_database_keeps_its_2020_onward_rows(ephemeral_db: str) -> None:
    _migrate(ephemeral_db, _before_066())
    with psycopg.connect(ephemeral_db) as connection:
        for calendar_id in _CME:
            last = connection.execute(
                "SELECT max(session_date) FROM trading_sessions WHERE calendar_id = %s",
                (calendar_id,),
            ).fetchone()[0]
            extend_calendar_sessions(connection, calendar_id, start=last, end=last)
        connection.commit()
    before = _dump(ephemeral_db, _UNTOUCHED_FROM)
    _migrate(ephemeral_db, MINUTE_MIGRATIONS)
    assert _dump(ephemeral_db, _UNTOUCHED_FROM) == before


@pytest.mark.parametrize(
    ("table", "insert_sql"),
    [
        pytest.param(
            "trading_holidays",
            "INSERT INTO trading_holidays (calendar_id, holiday_date, holiday_name, "
            "market_status) VALUES ('CME_EQUITY', '2019-12-25', 'x', 'closed')",
            id="holiday",
        ),
        pytest.param(
            "trading_sessions",
            "INSERT INTO trading_sessions (calendar_id, session_date, "
            "session_open_utc, session_close_utc) VALUES ('CME_METALS', "
            "'2019-12-30', '2019-12-29 23:00+00', '2019-12-30 22:00+00')",
            id="session",
        ),
    ],
)
def test_a_pre_2020_row_aborts_naming_the_counts(
    ephemeral_db: str, table: str, insert_sql: str
) -> None:
    _migrate(ephemeral_db, _before_066())
    with psycopg.connect(ephemeral_db) as connection:
        connection.execute(insert_sql)
        connection.commit()
    with pytest.raises(m066.CalendarHistoryError, match=f"{table} 1"):
        _migrate(ephemeral_db, MINUTE_MIGRATIONS)
    with psycopg.connect(ephemeral_db) as connection:
        assert not connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'trading_calendar_hours'"
        ).fetchone()


def test_a_changed_2020_session_aborts(
    ephemeral_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def extend_and_corrupt(conn: Any, calendar_id: str, **kwargs: Any) -> Any:
        result = extend_calendar_sessions(conn, calendar_id, **kwargs)
        conn.execute(
            "UPDATE trading_sessions SET session_close_utc = session_close_utc "
            "+ interval '1 minute' WHERE calendar_id = %s AND session_date = "
            "'2020-01-02'",
            (calendar_id,),
        )
        return result

    _migrate(ephemeral_db, _before_066())
    monkeypatch.setattr(m066, "extend_calendar_sessions", extend_and_corrupt)
    with pytest.raises(m066.CalendarHistoryError, match="changed"):
        _migrate(ephemeral_db, MINUTE_MIGRATIONS)


def test_a_latest_era_unlike_the_calendar_row_aborts(
    ephemeral_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    eras, exceptions = m066._HISTORY["CME_METALS"]
    wrong = HoursEra("CME_METALS", date(2015, 9, 21), time(17), time(16, 15),
                     time(17), "test")  # fmt: skip
    monkeypatch.setitem(m066._HISTORY, "CME_METALS", ((*eras[:-1], wrong), exceptions))
    _migrate(ephemeral_db, _before_066())
    with pytest.raises(m066.CalendarHistoryError, match="latest era"):
        _migrate(ephemeral_db, MINUTE_MIGRATIONS)
