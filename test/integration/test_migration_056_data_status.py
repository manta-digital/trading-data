"""Integration tests: the data_status view after migration 056 (slice 922).

Two meanings changed, and both are the kind that an operator reads and
believes, so they are asserted against a real view rather than rendered SQL:

**STALE means "not attempted in the last recorded universe walk."** The fixed
thresholds this replaces called every minute symbol STALE six days a week
once the minute pass moved to a weekly collecting cadence — a signal that is
always on tells you nothing. Now a symbol is STALE only if the last walk of
its granularity did not reach it.

**gap_count counts open gaps.** PROVIDER_HOLE is the provider's terminal
answer and RETRY_EXHAUSTED a terminal failure; counting either made
gap_count a number that could never reach zero, so it stopped meaning
anything an operator could act on.

Requires ``MT_TIMESCALE_TEST_URL``; the fixture creates and drops its own
database.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from manta_trading.data.quality.fetch_status import FetchStatus

_NOW = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)
_LAST_WALK = datetime(2026, 9, 12, 13, 5, tzinfo=UTC)
_BEFORE_WALK = _LAST_WALK - timedelta(hours=6)
_AFTER_WALK = _LAST_WALK + timedelta(minutes=30)


@pytest.fixture
def conn(migrated_db: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(migrated_db) as connection:
        yield connection


def _add_instrument(conn: psycopg.Connection, symbol: str) -> None:
    """One live instrument, which the view's symbols CTE will pick up.

    ``delisted_at_eodhd`` defaults to FALSE, which is what the view filters on.
    """
    conn.execute(
        "INSERT INTO instruments "
        "(canonical_id, symbol, asset_class, venue, eodhd_type, eodhd_exchange) "
        "VALUES (%s, %s, 'equity', 'NASDAQ', 'Common Stock', 'US') "
        "ON CONFLICT (canonical_id) DO NOTHING",
        (f"eq:{symbol}", symbol),
    )


def _attempt(
    conn: psycopg.Connection,
    symbol: str,
    granularity: str,
    at: datetime | None,
) -> None:
    """Stamp acquisition_state as a fetch would."""
    if at is None:
        return
    conn.execute(
        "INSERT INTO acquisition_state "
        "(symbol, granularity, provider, last_attempt_ts, last_attempt_outcome) "
        "VALUES (%s, %s, 'eodhd', %s, 'success') "
        "ON CONFLICT (symbol, granularity, provider) DO UPDATE "
        "SET last_attempt_ts = EXCLUDED.last_attempt_ts",
        (symbol, granularity, at),
    )


def _record_walk(
    conn: psycopg.Connection,
    pass_kind: str,
    anchor: datetime | None,
    *,
    ended: bool = True,
) -> None:
    conn.execute(
        "INSERT INTO pass_runs "
        "(run_id, pass, hostname, pid, walk_anchor_at, started_at, ended_at, "
        " outcome) "
        "VALUES (%s, %s, 'testhost', 1, %s, %s, %s, %s)",
        (
            uuid.uuid4(),
            pass_kind,
            anchor,
            anchor or _NOW,
            _NOW if ended else None,
            "COMPLETE" if ended else None,
        ),
    )


def _add_gap(
    conn: psycopg.Connection,
    symbol: str,
    granularity: str,
    status: FetchStatus,
    *,
    day: int = 1,
) -> None:
    """One gap row. ``day`` shifts the window so several can coexist."""
    conn.execute(
        "INSERT INTO data_gaps "
        "(symbol, granularity, gap_start, gap_end, fetch_status) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            symbol,
            granularity,
            _NOW - timedelta(days=day + 1),
            _NOW - timedelta(days=day),
            status.value,
        ),
    )


def _health(conn: psycopg.Connection, symbol: str, granularity: str) -> tuple[str, int]:
    row = conn.execute(
        "SELECT health, gap_count FROM data_status "
        "WHERE symbol = %s AND granularity = %s",
        (symbol, granularity),
    ).fetchone()
    assert row is not None, f"{symbol}/{granularity} missing from data_status"
    return row[0], row[1]


class TestStaleFromTheWalkAnchor:
    def test_attempted_after_the_walk_is_not_stale(
        self, conn: psycopg.Connection
    ) -> None:
        _add_instrument(conn, "FRESH")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "FRESH", "minute", _AFTER_WALK)
        health, _ = _health(conn, "FRESH", "minute")
        assert health == "OK"

    def test_attempted_before_the_walk_is_stale(self, conn: psycopg.Connection) -> None:
        """The walk happened and did not reach this symbol."""
        _add_instrument(conn, "MISSED")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "MISSED", "minute", _BEFORE_WALK)
        health, _ = _health(conn, "MISSED", "minute")
        assert health == "STALE"

    def test_never_attempted_is_stale(self, conn: psycopg.Connection) -> None:
        _add_instrument(conn, "COLD")
        _record_walk(conn, "minute", _LAST_WALK)
        health, _ = _health(conn, "COLD", "minute")
        assert health == "STALE"

    def test_with_no_anchored_run_only_never_attempted_is_stale(
        self, conn: psycopg.Connection
    ) -> None:
        """Nothing is known about when a walk last happened, so an old
        attempt is not evidence of staleness."""
        _add_instrument(conn, "OLDATTEMPT")
        _add_instrument(conn, "NOATTEMPT")
        _attempt(conn, "OLDATTEMPT", "minute", _NOW - timedelta(days=30))
        assert _health(conn, "OLDATTEMPT", "minute")[0] == "OK"
        assert _health(conn, "NOATTEMPT", "minute")[0] == "STALE"

    def test_an_unanchored_run_does_not_move_the_anchor(
        self, conn: psycopg.Connection
    ) -> None:
        """A backfill-only day attempts what it can reach; that is not a walk."""
        _add_instrument(conn, "BACKFILLONLY")
        _attempt(conn, "BACKFILLONLY", "minute", _NOW - timedelta(days=30))
        _record_walk(conn, "minute", None)
        assert _health(conn, "BACKFILLONLY", "minute")[0] == "OK"

    def test_an_open_run_does_not_move_the_anchor(
        self, conn: psycopg.Connection
    ) -> None:
        """A pass still running has not finished walking."""
        _add_instrument(conn, "MIDWALK")
        _attempt(conn, "MIDWALK", "minute", _BEFORE_WALK)
        _record_walk(conn, "minute", _LAST_WALK, ended=False)
        assert _health(conn, "MIDWALK", "minute")[0] == "OK"

    def test_the_newest_anchor_wins(self, conn: psycopg.Connection) -> None:
        _add_instrument(conn, "TWOWALKS")
        _record_walk(conn, "minute", _LAST_WALK - timedelta(days=7))
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "TWOWALKS", "minute", _BEFORE_WALK)
        assert _health(conn, "TWOWALKS", "minute")[0] == "STALE"

    def test_granularities_do_not_share_an_anchor(
        self, conn: psycopg.Connection
    ) -> None:
        """A minute walk says nothing about whether daily symbols were
        attempted; before 056 one interval governed both."""
        _add_instrument(conn, "BOTH")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "BOTH", "minute", _AFTER_WALK)
        _attempt(conn, "BOTH", "daily", _NOW - timedelta(days=30))
        assert _health(conn, "BOTH", "minute")[0] == "OK"
        # No daily walk recorded, so the old daily attempt is not judged.
        assert _health(conn, "BOTH", "daily")[0] == "OK"

    def test_both_daily_firings_share_one_boundary(
        self, conn: psycopg.Connection
    ) -> None:
        """A symbol attempted by the 00:35 pass must not go STALE when the
        12:35 pass ends — both carry the same pass boundary as their anchor."""
        boundary = datetime(2026, 9, 12, 0, 35, tzinfo=UTC)
        _add_instrument(conn, "DAILYSYM")
        _record_walk(conn, "daily", boundary)
        _attempt(conn, "DAILYSYM", "daily", boundary + timedelta(minutes=5))
        _record_walk(conn, "daily", boundary)
        assert _health(conn, "DAILYSYM", "daily")[0] == "OK"


class TestGapCountMeansOpenGaps:
    def test_an_unknown_gap_counts(self, conn: psycopg.Connection) -> None:
        _add_instrument(conn, "UNK")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "UNK", "minute", _AFTER_WALK)
        _add_gap(conn, "UNK", "minute", FetchStatus.UNKNOWN)
        health, count = _health(conn, "UNK", "minute")
        assert (health, count) == ("GAPS", 1)

    def test_a_retryable_failure_counts(self, conn: psycopg.Connection) -> None:
        _add_instrument(conn, "RETRY")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "RETRY", "minute", _AFTER_WALK)
        _add_gap(conn, "RETRY", "minute", FetchStatus.FAILED_RETRYABLE)
        health, count = _health(conn, "RETRY", "minute")
        assert (health, count) == ("GAPS", 1)

    def test_a_provider_hole_reads_ok_with_no_gaps(
        self, conn: psycopg.Connection
    ) -> None:
        """The provider answered: there is nothing there. Not an open question."""
        _add_instrument(conn, "HOLE")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "HOLE", "minute", _AFTER_WALK)
        _add_gap(conn, "HOLE", "minute", FetchStatus.PROVIDER_HOLE)
        health, count = _health(conn, "HOLE", "minute")
        assert (health, count) == ("OK", 0)

    def test_retry_exhausted_still_reads_failed(self, conn: psycopg.Connection) -> None:
        """Terminal failure keeps its verdict; only the count changed."""
        _add_instrument(conn, "EXHAUSTED")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "EXHAUSTED", "minute", _AFTER_WALK)
        _add_gap(conn, "EXHAUSTED", "minute", FetchStatus.RETRY_EXHAUSTED)
        health, count = _health(conn, "EXHAUSTED", "minute")
        assert health == "FAILED"
        assert count == 0

    def test_a_mix_counts_only_the_open_ones(self, conn: psycopg.Connection) -> None:
        _add_instrument(conn, "MIXED")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "MIXED", "minute", _AFTER_WALK)
        _add_gap(conn, "MIXED", "minute", FetchStatus.UNKNOWN, day=1)
        _add_gap(conn, "MIXED", "minute", FetchStatus.FAILED_RETRYABLE, day=3)
        _add_gap(conn, "MIXED", "minute", FetchStatus.PROVIDER_HOLE, day=5)
        health, count = _health(conn, "MIXED", "minute")
        assert (health, count) == ("GAPS", 2)

    def test_a_symbol_with_only_holes_can_reach_zero(
        self, conn: psycopg.Connection
    ) -> None:
        """The property the old count could never have: it reaches zero."""
        _add_instrument(conn, "ALLHOLES")
        _record_walk(conn, "minute", _LAST_WALK)
        _attempt(conn, "ALLHOLES", "minute", _AFTER_WALK)
        for day in (1, 3, 5):
            _add_gap(conn, "ALLHOLES", "minute", FetchStatus.PROVIDER_HOLE, day=day)
        assert _health(conn, "ALLHOLES", "minute") == ("OK", 0)


class TestTheViewContractHolds:
    def test_the_column_list_is_unchanged(self, conn: psycopg.Connection) -> None:
        """D2: CREATE OR REPLACE means no column added, renamed or reordered."""
        names = [
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'data_status' ORDER BY ordinal_position"
            ).fetchall()
        ]
        assert names == [
            "symbol",
            "granularity",
            "trading_calendar_id",
            "first_bar_ts",
            "last_bar_ts",
            "bars_stored",
            "target_end_ts",
            "effective_start",
            "gap_count",
            "has_retry_exhausted",
            "last_attempt_ts",
            "last_attempt_outcome",
            "health",
        ]

    def test_the_doc_comment_states_both_new_rules(
        self, conn: psycopg.Connection
    ) -> None:
        row = conn.execute(
            "SELECT obj_description('data_status'::regclass, 'pg_class')"
        ).fetchone()
        assert row is not None and row[0] is not None
        comment = row[0]
        assert "STALE MEANS NOT ATTEMPTED IN THE LAST RECORDED WALK" in comment
        assert "GAP_COUNT MEANS OPEN GAPS" in comment

    def test_056_is_recorded_as_applied(self, conn: psycopg.Connection) -> None:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = %s",
            ("056_data_status_open_gaps_and_walk_anchor",),
        ).fetchone()
        assert row is not None
