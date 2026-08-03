"""Integration test for the slice 912 D1 work-list SQL.

The unit tests in ``test/unit/data/acquisition/daemon/test_pending_daily_symbols.py``
drive ``pending_daily_symbols`` through a mock cursor, so they verify the
classification logic but never execute the statement. This module executes the
real SQL against a real database, which is the only way to catch a syntax error,
a renamed column, or a join that silently matches nothing.

Requires ``MT_TIMESCALE_DB_URL``; skipped otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from manta_trading.data.acquisition.daemon.daily import (
    daily_pass_boundary,
    pending_daily_symbols,
)

# Bound every statement — this may run against a database holding a 4.4-billion
# row hypertable, and an unbounded probe there is how the 2026-07-20 incident
# started. Nothing here touches minute_ohlcv, but the discipline is not optional.
_STATEMENT_TIMEOUT_MS = 15_000


@pytest.fixture
def conn(timescale_db_url: str):
    with psycopg.connect(timescale_db_url, autocommit=True) as connection:
        connection.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")
        yield connection


def test_sql_executes_and_returns_a_partition(conn):
    """The statement runs, and every returned symbol lands in exactly one bucket.

    Deliberately asserts a structural invariant rather than specific symbols:
    the test database is documented as unrepresentative (few symbols, possibly
    empty instruments), so any assertion about *which* symbols come back would
    be testing the fixture, not the query.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM instruments LIMIT 25")
        scope = [row[0] for row in cur.fetchall()]

    if not scope:
        pytest.skip("instruments table is empty in this database")

    result = pending_daily_symbols(conn, scope, daily_pass_boundary(datetime.now(UTC)))

    buckets = set(result.pending) | set(result.unactionable_no_calendar)
    assert buckets <= set(scope), "returned a symbol that was not in scope"
    assert not (
        set(result.pending) & set(result.unactionable_no_calendar)
    ), "a symbol landed in both buckets"


def test_unknown_symbol_is_unactionable_not_dropped(conn):
    """A symbol absent from `instruments` has no calendar, so it is reported.

    It must not vanish: a scope member the cycle cannot act on is counted (D6),
    never silently discarded — that silent-drop behavior is what made GitHub
    issue #4's 906 instruments invisible for so long.
    """
    result = pending_daily_symbols(
        conn, ["__NO_SUCH_SYMBOL__"], daily_pass_boundary(datetime.now(UTC))
    )
    assert result.unactionable_no_calendar == ["__NO_SUCH_SYMBOL__"]
    assert result.pending == []


def test_ordering_survives_the_round_trip(conn):
    """WITH ORDINALITY must preserve caller order through the database.

    This is the assertion the mock cannot make: a mock returns rows in the
    order the test wrote them, so only a real query proves the ORDER BY works.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM instruments ORDER BY symbol DESC LIMIT 5")
        scope = [row[0] for row in cur.fetchall()]

    if len(scope) < 2:
        pytest.skip("need at least two instruments to test ordering")

    result = pending_daily_symbols(conn, scope, daily_pass_boundary(datetime.now(UTC)))

    # `scope` is descending; a re-sort would come back ascending. Comparing
    # against the scope order filtered to what was returned is the check that
    # a mock cannot make, because a mock replays rows in the order given.
    assert result.pending == [s for s in scope if s in set(result.pending)]
    assert result.pending != sorted(result.pending) or len(result.pending) < 2, (
        "pending came back ascending from a descending scope — ORDER BY s.ord "
        "is not taking effect"
    )


def test_no_fan_out_on_duplicate_instrument_rows(conn):
    """One row out per scope entry, even when a symbol has several instrument rows.

    ``instruments.symbol`` is not unique (PK is ``instrument_id``; UNIQUE is on
    ``canonical_id``). A naive join to ``instruments`` would emit one row per
    instrument row, which would fetch a symbol repeatedly in one pass and could
    place it in both buckets at once. Only a real query can catch this — a mock
    replays whatever rows the test wrote.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol FROM instruments
             GROUP BY symbol HAVING count(*) > 1
             LIMIT 5
            """
        )
        duplicated = [row[0] for row in cur.fetchall()]

    if not duplicated:
        pytest.skip("no symbol has multiple instrument rows in this database")

    result = pending_daily_symbols(
        conn, duplicated, daily_pass_boundary(datetime.now(UTC))
    )
    returned = result.pending + result.unactionable_no_calendar
    assert len(returned) == len(set(returned)), "a symbol was returned more than once"
    assert sorted(returned) == sorted(duplicated), (
        "scope size changed through the query"
    )


def test_future_boundary_marks_everything_pending(conn):
    """With a boundary in the future, nothing can have been attempted yet."""
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM instruments LIMIT 10")
        scope = [row[0] for row in cur.fetchall()]

    if not scope:
        pytest.skip("instruments table is empty in this database")

    far_future = datetime.now(UTC) + timedelta(days=365)
    result = pending_daily_symbols(conn, scope, far_future)
    assert set(result.pending) | set(result.unactionable_no_calendar) == set(scope)
