"""The slice-921 repair's database behavior, against a real database.

**Why this exists when ``test/unit/test_repair_921.py`` is green.** Every SC2
property is a property of ``update_data_gaps`` itself:

- ``force_reset_terminal=True`` over the window resets the in-window terminal
  rows and ONLY those;
- carry-forward (``_best_prior_count``, keyed on ``gap_start``) preserves
  ``attempt_count``, so a second ``--apply`` is genuinely zero net change;
- rows before ``REPAIR_921_WINDOW_START`` survive untouched.

The unit suite fakes that writer, so it stands in for exactly the code these
claims are about. The specific failure this catches: carry-forward keys on a
``gap_start`` the recomputed range no longer matches, so every repaired row
restarts at ``attempt_count = 0`` or re-increments toward RETRY_EXHAUSTED —
and the fake-writer suite stays green the whole time.

Requires ``MT_TIMESCALE_TEST_URL``; ``migrated_db`` creates and drops its own
throwaway database, so nothing here can touch production.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from manta_trading.constants import REPAIR_921_WINDOW_START
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.repair_921 import window_start_utc
from manta_trading.data.gaps.update_data_gaps import update_data_gaps
from manta_trading.data.quality.fetch_status import FetchStatus

SYMBOL = "RPR921"
CALENDAR = "NYSE"

#: A session well before the repair window — its rows must never move.
PRE_WINDOW_DAY = date(2026, 6, 10)
#: Sessions inside the window.
IN_WINDOW_DAYS = (date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22))
#: A session whose only bar is the prior day's 20:00 ET after-hours bar,
#: which EODHD dates 00:00 UTC on this day (#22): covered to the coarse
#: index, empty to the session.
SPILLOVER_DAY = date(2026, 7, 23)


def _open(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)


def _close(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 20, 0, tzinfo=UTC)


def _seed_fixture(conn: psycopg.Connection) -> None:
    """A production-shaped fixture spanning the window boundary."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trading_calendars "
            "(calendar_id, exchange_name, timezone, market_open, market_close, "
            " has_extended_hours) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (CALENDAR, "NYSE", "America/New_York", "09:30", "16:00", True),
        )
        for day in (PRE_WINDOW_DAY, *IN_WINDOW_DAYS, SPILLOVER_DAY):
            cur.execute(
                "INSERT INTO trading_sessions "
                "(calendar_id, session_date, session_open_utc, session_close_utc) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (CALENDAR, day, _open(day), _close(day)),
            )
        cur.execute(
            "INSERT INTO instruments "
            "(canonical_id, symbol, asset_class, venue, trading_calendar_id, "
            " delisted_at_eodhd, eodhd_type, eodhd_exchange, first_listing_date) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                f"EQ:{SYMBOL}",
                SYMBOL,
                "equity",
                "US",
                CALENDAR,
                False,
                "Common Stock",
                "US",
                date(2020, 1, 1),
            ),
        )

        # A truncated day: one bar, at the session open.
        truncated = IN_WINDOW_DAYS[0]
        cur.execute(
            "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, volume) "
            "VALUES (%s,%s,10,10,10,10,100) ON CONFLICT DO NOTHING",
            (_open(truncated), SYMBOL),
        )
        # The spillover shape: one bar at 00:00 UTC, none inside the session.
        cur.execute(
            "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, volume) "
            "VALUES (%s,%s,10,10,10,10,100) ON CONFLICT DO NOTHING",
            (datetime(2026, 7, 23, 0, 0, tzinfo=UTC), SYMBOL),
        )
        # A healthy day: bars through the session.
        healthy = IN_WINDOW_DAYS[1]
        for minute in range(0, 300, 10):
            cur.execute(
                "INSERT INTO minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s,%s,10,10,10,10,100) ON CONFLICT DO NOTHING",
                (_open(healthy) + timedelta(minutes=minute), SYMBOL),
            )

        # Gap rows: one before the window (must survive byte-identical), one
        # terminal row inside it, and one straddling the boundary.
        rows = [
            (
                _open(PRE_WINDOW_DAY),
                _close(PRE_WINDOW_DAY),
                str(FetchStatus.PROVIDER_HOLE),
                4,
            ),
            (
                _open(IN_WINDOW_DAYS[2]),
                _close(IN_WINDOW_DAYS[2]),
                str(FetchStatus.RETRY_EXHAUSTED),
                5,
            ),
            # Straddles REPAIR_921_WINDOW_START (2026-07-16).
            (
                datetime(2026, 7, 10, 13, 30, tzinfo=UTC),
                datetime(2026, 7, 20, 20, 0, tzinfo=UTC),
                str(FetchStatus.UNKNOWN),
                2,
            ),
        ]
        for gap_start, gap_end, status, attempts in rows:
            cur.execute(
                "INSERT INTO data_gaps "
                "(symbol, granularity, gap_start, gap_end, fetch_status, "
                " last_attempt_ts, attempt_count) "
                "VALUES (%s,'minute',%s,%s,%s,now(),%s) ON CONFLICT DO NOTHING",
                (SYMBOL, gap_start, gap_end, status, attempts),
            )
    conn.commit()


def _gap_rows(conn: psycopg.Connection) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gap_start, gap_end, fetch_status, attempt_count "
            "FROM data_gaps WHERE symbol = %s AND granularity = 'minute' "
            "ORDER BY gap_start, gap_end",
            (SYMBOL,),
        )
        return list(cur.fetchall())


def _apply_once(conn: psycopg.Connection, *, window_start: datetime) -> None:
    """One repair pass through the single writer, as the script does it."""
    from manta_trading.data.gaps.minute_coverage import (
        compute_missing_minute_sessions,
    )

    # The coverage index as the cagg would report it: the truncated day looks
    # covered (it holds a bar), so the repair must force it uncovered.
    coverage = {SYMBOL: {IN_WINDOW_DAYS[0], IN_WINDOW_DAYS[1]}}
    now_midnight = datetime(2026, 9, 9, tzinfo=UTC)
    ranges = compute_missing_minute_sessions(
        conn,
        SYMBOL,
        coverage,
        window_start,
        now_midnight,
        uncovered_days={IN_WINDOW_DAYS[0]},
        # As the script does: the same write force-resets terminal rows.
        respect_terminal_rows=False,
    )
    with conn.transaction():
        update_data_gaps(
            conn,
            SYMBOL,
            "minute",
            window_start,
            now_midnight,
            fetch_status_for_unfilled=FetchStatus.UNKNOWN,
            outcome=LastAttemptOutcome.PARTIAL,
            force_reset_terminal=True,
            precomputed_ranges=ranges,
        )


@pytest.fixture()
def seeded(migrated_db: str):
    with psycopg.connect(migrated_db) as conn:
        _seed_fixture(conn)
        yield conn


class TestRepairIsIdempotent:
    def test_a_second_apply_is_zero_net_change(self, seeded) -> None:
        """SC2. Carry-forward keys on gap_start; if the recomputed range no
        longer matches it, every repaired row restarts its attempt count and
        the second run differs from the first."""
        _apply_once(seeded, window_start=window_start_utc())
        after_first = _gap_rows(seeded)
        _apply_once(seeded, window_start=window_start_utc())
        after_second = _gap_rows(seeded)
        assert after_first == after_second

    def test_attempt_counts_do_not_climb_across_runs(self, seeded) -> None:
        """A repair that re-increments walks rows toward RETRY_EXHAUSTED
        without the provider ever being asked."""
        _apply_once(seeded, window_start=window_start_utc())
        first = {(r[0], r[1]): r[3] for r in _gap_rows(seeded)}
        for _ in range(3):
            _apply_once(seeded, window_start=window_start_utc())
        later = {(r[0], r[1]): r[3] for r in _gap_rows(seeded)}
        assert first == later


class TestPreWindowRowsSurvive:
    def test_a_row_before_the_window_is_byte_identical_afterwards(self, seeded) -> None:
        """SC2: the repair must not reset provider holes accumulated over
        years of backfill."""
        before = [r for r in _gap_rows(seeded) if r[0] < window_start_utc()]
        assert before, "the fixture must seed a pre-window row"
        _apply_once(seeded, window_start=window_start_utc())
        after = [r for r in _gap_rows(seeded) if r[1] <= window_start_utc()]
        assert any(row in after for row in before), (
            "the pre-window PROVIDER_HOLE row must survive unchanged"
        )

    def test_the_pre_window_row_keeps_its_terminal_status(self, seeded) -> None:
        _apply_once(seeded, window_start=window_start_utc())
        with seeded.cursor() as cur:
            cur.execute(
                "SELECT fetch_status, attempt_count FROM data_gaps "
                "WHERE symbol = %s AND gap_start = %s",
                (SYMBOL, _open(PRE_WINDOW_DAY)),
            )
            row = cur.fetchone()
        assert row is not None, "the pre-window row was deleted"
        assert row[0] == str(FetchStatus.PROVIDER_HOLE)
        assert row[1] == 4


class TestTerminalRowsInsideTheWindow:
    def test_an_in_window_terminal_row_is_reset(self, seeded) -> None:
        """A RETRY_EXHAUSTED verdict reached from a truncated fetch is wrong
        and must not stay terminal."""
        _apply_once(seeded, window_start=window_start_utc())
        statuses = {r[2] for r in _gap_rows(seeded) if r[0] >= window_start_utc()}
        assert str(FetchStatus.RETRY_EXHAUSTED) not in statuses


class TestStraddlingRow:
    def test_the_default_window_leaves_the_straddling_row_behind(self, seeded) -> None:
        """Task 6.3's premise, proven against the real writer:
        _delete_intersecting is containment, so a row starting before the
        window survives and overlaps the freshly-seeded rows."""
        _apply_once(seeded, window_start=window_start_utc())
        straddler = [
            r
            for r in _gap_rows(seeded)
            if r[0] == datetime(2026, 7, 10, 13, 30, tzinfo=UTC)
        ]
        assert straddler, "the straddling row survives the default window"

    def test_widening_the_window_leaves_no_overlapping_rows(self, seeded) -> None:
        """Task 6.3's decision, stated as the property that matters.

        Widening back to the straddling row's own ``gap_start`` puts it inside
        ``_delete_intersecting``'s containment predicate, so it is deleted and
        the window is then re-seeded from one consistent diff. The row may
        legitimately reappear with the same bounds — its sessions really are
        missing — but it can no longer coexist with separately-seeded rows
        covering the same sessions, which is the double-fetch this decision
        exists to prevent.
        """
        widened = datetime(2026, 7, 10, 13, 30, tzinfo=UTC)
        _apply_once(seeded, window_start=widened)
        rows = _gap_rows(seeded)

        overlaps = [
            (a, b)
            for i, a in enumerate(rows)
            for b in rows[i + 1 :]
            if a[0] < b[1] and b[0] < a[1]
        ]
        assert not overlaps, f"overlapping gap rows would be fetched twice: {overlaps}"

    def test_widening_is_bounded_and_spares_the_pre_window_row(self, seeded) -> None:
        """The window moves back to that row's start and no further, so
        provider holes behind it are not pulled into the reset."""
        widened = datetime(2026, 7, 10, 13, 30, tzinfo=UTC)
        _apply_once(seeded, window_start=widened)
        with seeded.cursor() as cur:
            cur.execute(
                "SELECT fetch_status, attempt_count FROM data_gaps "
                "WHERE symbol = %s AND gap_start = %s",
                (SYMBOL, _open(PRE_WINDOW_DAY)),
            )
            row = cur.fetchone()
        assert row is not None, "the June row is behind the widened start"
        assert row[0] == str(FetchStatus.PROVIDER_HOLE)
        assert row[1] == 4

    def test_the_default_window_does_leave_an_overlap(self, seeded) -> None:
        """The counterfactual that justifies widening at all: with the default
        window the straddling row survives AND the in-window sessions it
        covers are seeded separately, so those sessions are fetched twice."""
        _apply_once(seeded, window_start=window_start_utc())
        rows = _gap_rows(seeded)
        overlaps = [
            (a, b)
            for i, a in enumerate(rows)
            for b in rows[i + 1 :]
            if a[0] < b[1] and b[0] < a[1]
        ]
        assert overlaps, (
            "if this stops overlapping, _delete_intersecting's semantics "
            "changed and Task 6.3's widening may no longer be needed"
        )


class TestTruncationPredicateAgainstRealBars:
    """#22: both the bar-at-open shape and the 00:00 UTC spillover shape are
    truncated; a healthy session is not; both predicate implementations agree
    on real rows, not just on SQL text."""

    def test_per_symbol_and_universe_agree_and_see_both_shapes(self, seeded) -> None:
        from manta_trading.data.gaps.repair_921 import (
            build_truncated_day_index,
            find_truncated_days,
        )

        conn = seeded
        per_symbol = find_truncated_days(conn, SYMBOL, since=_open(IN_WINDOW_DAYS[0]))
        universe = build_truncated_day_index(conn, since=_open(IN_WINDOW_DAYS[0]))
        assert per_symbol == frozenset({IN_WINDOW_DAYS[0], SPILLOVER_DAY})
        assert universe.get(SYMBOL) == per_symbol
