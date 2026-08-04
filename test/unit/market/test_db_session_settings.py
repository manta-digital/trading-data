"""Session-settings plumbing for the two OHLCV DB classes (slice 186 D1).

No database is required: the pool is never opened, and the ``configure``
callable a pool would invoke is called directly against a recording double.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from manta_trading.constants import (
    API_SERVING_SESSION,
    DB_BULK_SESSION,
    DbSessionSettings,
)
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_CONNINFO = "postgresql://user:pass@localhost:5432/nonexistent"

_MINUTE_EXTRA_SETS = (
    "SET max_parallel_workers_per_gather = 8",
    "SET enable_partitionwise_aggregate = on",
)


class RecordingConnection:
    """Stands in for a psycopg connection during pool ``configure``."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.autocommit_history: list[bool] = []
        self._autocommit = False

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self._autocommit = value
        self.autocommit_history.append(value)

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def _configure(db: Any) -> RecordingConnection:
    """Invoke the instance's pool ``configure`` callable and return the double."""
    conn = RecordingConnection()
    db._configure_connection(conn)
    return conn


@pytest.fixture(params=[TimescaleMinuteDataDB, TimescaleDailyDataDB])
def db_class(request: pytest.FixtureRequest) -> type:
    return request.param  # type: ignore[no-any-return]


def _build(db_class: type, session: DbSessionSettings | None = None) -> Any:
    """Construct a DB instance with pool creation suppressed."""
    kwargs = {} if session is None else {"session": session}
    with patch.object(db_class, "_init_pool"):
        return db_class(_CONNINFO, **kwargs)


def test_default_construction_emits_the_bulk_values(db_class: type) -> None:
    """The CLI/daemon regression guard.

    Every existing caller constructs these classes positionally with only a
    conninfo. If this assertion ever fails, a bulk COPY or a universe-wide
    aggregation silently inherited the API's serving budget.
    """
    conn = _configure(_build(db_class))
    assert "SET work_mem = '512MB'" in conn.statements
    assert "SET statement_timeout = '300s'" in conn.statements
    assert DB_BULK_SESSION.work_mem == "512MB"
    assert DB_BULK_SESSION.statement_timeout == "300s"


def test_serving_session_emits_the_api_values(db_class: type) -> None:
    conn = _configure(_build(db_class, API_SERVING_SESSION))
    assert "SET work_mem = '64MB'" in conn.statements
    assert "SET statement_timeout = '20s'" in conn.statements
    assert "SET work_mem = '512MB'" not in conn.statements


def test_arbitrary_session_values_are_honored(db_class: type) -> None:
    """The seam carries whatever it is given — it is not a two-value switch."""
    conn = _configure(_build(db_class, DbSessionSettings("8MB", "1500ms")))
    assert "SET work_mem = '8MB'" in conn.statements
    assert "SET statement_timeout = '1500ms'" in conn.statements


def test_timezone_and_autocommit_toggle_are_unchanged(db_class: type) -> None:
    """Everything other than the two plumbed values must be untouched."""
    conn = _configure(_build(db_class, API_SERVING_SESSION))
    assert conn.statements[0] == "SET timezone = 'UTC'"
    assert conn.autocommit_history == [True, False]


@pytest.mark.parametrize(
    "session", [None, API_SERVING_SESSION], ids=["default", "serving"]
)
def test_minute_class_keeps_its_two_extra_sets(
    session: DbSessionSettings | None,
) -> None:
    """Parallelism and partitionwise aggregation are properties of the minute
    workload, not of the session budget, and are deliberately not parameterized.
    """
    conn = _configure(_build(TimescaleMinuteDataDB, session))
    for statement in _MINUTE_EXTRA_SETS:
        assert statement in conn.statements


@pytest.mark.parametrize(
    "session", [None, API_SERVING_SESSION], ids=["default", "serving"]
)
def test_daily_class_has_no_extra_sets(session: DbSessionSettings | None) -> None:
    conn = _configure(_build(TimescaleDailyDataDB, session))
    assert len(conn.statements) == 3
    for statement in _MINUTE_EXTRA_SETS:
        assert statement not in conn.statements


def test_pool_receives_the_bound_configure_callable(db_class: type) -> None:
    """``_configure_connection`` was a ``@staticmethod`` before 186 and could
    not see the instance. Passing an unbound function to ``configure=`` would
    silently keep the old values, so assert the pool gets a callable that
    reflects this instance's session."""
    module = db_class.__module__
    with patch(f"{module}.ConnectionPool") as pool_cls:
        db_class(_CONNINFO, session=API_SERVING_SESSION)

    configure = pool_cls.call_args.kwargs["configure"]
    assert configure.__self__ is not None  # bound, not a plain function
    conn = RecordingConnection()
    configure(conn)
    assert "SET statement_timeout = '20s'" in conn.statements


def test_pool_sizes_are_unchanged(db_class: type) -> None:
    """D2 defers pool sizing to slice 187; this slice must not move it."""
    expected = {
        TimescaleMinuteDataDB: (4, 10),
        TimescaleDailyDataDB: (2, 8),
    }[db_class]
    with patch(f"{db_class.__module__}.ConnectionPool") as pool_cls:
        db_class(_CONNINFO, session=API_SERVING_SESSION)
    kwargs = pool_cls.call_args.kwargs
    assert (kwargs["min_size"], kwargs["max_size"]) == expected


def test_construction_still_accepts_a_bare_conninfo(db_class: type) -> None:
    """Every CLI and daemon call site passes one positional argument."""
    with patch.object(db_class, "_init_pool") as init_pool:
        instance = db_class(_CONNINFO)
    assert instance.conninfo == _CONNINFO
    assert init_pool.called
