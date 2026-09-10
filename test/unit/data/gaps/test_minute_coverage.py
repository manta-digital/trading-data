"""Unit tests for minute_coverage (slice 162 coverage-aware minute seeding).

Uses a mocked psycopg connection; no live DB required. Mocks the DB I/O
boundary (cursor execute/fetchall, patched clamp/session helpers) and tests
the diff/grouping logic with real data.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from manta_trading.constants import GRANULARITY_SOURCE, Granularity
from manta_trading.data.gaps.compute_missing_ranges import fetch_session_bounds
from manta_trading.data.gaps.minute_coverage import (
    build_minute_coverage_index,
    build_symbol_minute_coverage,
    compute_missing_minute_sessions,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)

UTC = timezone.utc


def _dt(y: int, m: int, d: int, h: int = 14, mi: int = 30) -> datetime:
    return datetime(y, m, d, h, mi, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# build_minute_coverage_index
# ---------------------------------------------------------------------------


def _make_index_conn(rows: list[tuple[str, date]]) -> MagicMock:
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall = MagicMock(return_value=rows)
    conn.cursor = MagicMock(return_value=cur)
    return conn


def _fresh_verdict(view_name: str = "minute_4hour_ohlcv") -> FreshnessVerdict:
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=True,
        signals=(),
        lag=timedelta(hours=2),
        threshold=timedelta(days=1),
        detail="fresh",
    )


def _stale_verdict(view_name: str = "minute_4hour_ohlcv") -> FreshnessVerdict:
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=False,
        signals=(
            StalenessSignal.NOT_SCHEDULED,
            StalenessSignal.LAG_EXCEEDS_THRESHOLD,
        ),
        lag=timedelta(days=4),
        threshold=timedelta(days=1),
        detail="stale",
    )


class TestBuildMinuteCoverageIndex:
    """Slice 168 added a freshness guard ahead of the coverage query, so these
    slice-162 tests stub it fresh to keep exercising the query path itself.
    The guard's own behavior is covered by TestCoverageFreshnessGuard below."""

    @pytest.fixture(autouse=True)
    def _guard_passes(self):
        with patch(
            "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
            return_value=_fresh_verdict(),
        ):
            yield

    def test_groups_rows_by_symbol(self) -> None:
        """date_trunc('day', ...) returns a timestamptz, not a date — psycopg
        hands back datetime rows here, matching production. A prior version
        of this fixture used plain `date(...)` rows, which masked a real bug:
        the index stored raw datetimes while the diff compared against
        `session.date()` (a plain date), so nothing ever matched and every
        symbol was treated as fully uncovered."""
        rows = [
            (
                "AAPL",
                datetime(2024, 1, 2, tzinfo=UTC),
            ),
            (
                "AAPL",
                datetime(2024, 1, 3, tzinfo=UTC),
            ),
            (
                "MSFT",
                datetime(2024, 1, 2, tzinfo=UTC),
            ),
        ]
        conn = _make_index_conn(rows)
        result = build_minute_coverage_index(conn)
        assert result == {
            "AAPL": {date(2024, 1, 2), date(2024, 1, 3)},
            "MSFT": {date(2024, 1, 2)},
        }
        # Every value must be a plain date, not a datetime — otherwise
        # compute_missing_minute_sessions' `session.date() not in covered_days`
        # check silently never matches.
        for covered in result.values():
            for day in covered:
                assert type(day) is date

    def test_empty_cagg_returns_empty_dict_not_none(self) -> None:
        conn = _make_index_conn([])
        result = build_minute_coverage_index(conn)
        assert result == {}
        assert result is not None

    def test_query_timeout_returns_none(self) -> None:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)

        def _execute(sql: str) -> None:
            if "GROUP BY" in sql:
                raise psycopg.errors.QueryCanceled("statement timeout")

        cur.execute = MagicMock(side_effect=_execute)
        conn.cursor = MagicMock(return_value=cur)

        result = build_minute_coverage_index(conn)
        assert result is None

    def test_uses_h4_cagg_name_from_granularity_source(self) -> None:
        conn = _make_index_conn([])
        cur = conn.cursor.return_value
        build_minute_coverage_index(conn)
        executed_sql = " ".join(
            call.args[0] for call in cur.execute.call_args_list if call.args
        )
        assert "minute_4hour_ohlcv" in executed_sql


# ---------------------------------------------------------------------------
# build_symbol_minute_coverage (slice 165)
# ---------------------------------------------------------------------------


class TestBuildSymbolMinuteCoverage:
    """Per-symbol sibling of build_minute_coverage_index (slice 165): same
    guard/timeout/fail-safe contract, single-symbol scope. Rows from the
    per-symbol query are one-tuples (day only — symbol is the filter)."""

    @pytest.fixture(autouse=True)
    def _guard_passes(self):
        with patch(
            "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
            return_value=_fresh_verdict(),
        ):
            yield

    def _make_conn(self, rows: list[tuple]) -> MagicMock:
        return _make_index_conn(rows)

    def test_returns_single_symbol_days_normalized_to_date(self) -> None:
        """date_trunc rows arrive as datetimes; values must normalize to
        plain dates or compute_missing_minute_sessions never matches."""
        rows = [
            (datetime(2024, 1, 2, tzinfo=UTC),),
            (datetime(2024, 1, 3, tzinfo=UTC),),
        ]
        conn = self._make_conn(rows)
        result = build_symbol_minute_coverage(conn, "AAPL")
        assert result == {"AAPL": {date(2024, 1, 2), date(2024, 1, 3)}}
        for day in result["AAPL"]:
            assert type(day) is date

    def test_no_coverage_returns_empty_set_not_none(self) -> None:
        conn = self._make_conn([])
        result = build_symbol_minute_coverage(conn, "AAPL")
        assert result == {"AAPL": set()}
        assert result is not None

    def test_query_timeout_returns_none(self) -> None:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)

        def _execute(sql: str, *params) -> None:
            if "GROUP BY" in sql:
                raise psycopg.errors.QueryCanceled("statement timeout")

        cur.execute = MagicMock(side_effect=_execute)
        conn.cursor = MagicMock(return_value=cur)

        result = build_symbol_minute_coverage(conn, "AAPL")
        assert result is None

    def test_stale_cagg_returns_none_without_running_query(self) -> None:
        conn = self._make_conn([(datetime(2024, 1, 2, tzinfo=UTC),)])
        cur = conn.cursor.return_value
        with patch(
            "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
            return_value=_stale_verdict(),
        ):
            result = build_symbol_minute_coverage(conn, "AAPL")
        assert result is None
        cur.execute.assert_not_called()

    def test_symbol_passed_as_parameter_not_interpolated(self) -> None:
        conn = self._make_conn([])
        cur = conn.cursor.return_value
        build_symbol_minute_coverage(conn, "AAPL")
        query_calls = [
            call
            for call in cur.execute.call_args_list
            if call.args and "GROUP BY" in call.args[0]
        ]
        assert len(query_calls) == 1
        sql, params = query_calls[0].args
        assert "AAPL" not in sql
        assert params == ("AAPL",)
        assert "minute_4hour_ohlcv" in sql


# ---------------------------------------------------------------------------
# compute_missing_minute_sessions
# ---------------------------------------------------------------------------


# Real trading_sessions values in both DST regimes (slice 921 SC1). The close
# must be READ, never computed as open + a fixed offset: EST and EDT sessions
# differ in UTC, and an early close differs in span.
_REGULAR_SPAN = timedelta(minutes=390)


def _closes_for(sessions: list[datetime]) -> dict[datetime, datetime]:
    """Default open→close mapping: a regular 6.5-hour session per open."""
    return {s: s + _REGULAR_SPAN for s in sessions}


def _patched_run(
    *,
    lifecycle_from: datetime,
    lifecycle_to: datetime,
    sessions: list[datetime],
    coverage_index: dict[str, set[date]],
    symbol: str = "AAPL",
    session_closes: dict[datetime, datetime] | None = None,
    uncovered_days: set[date] | None = None,
    terminal_rows: list[tuple[datetime, datetime]] | None = None,
):
    conn = MagicMock()
    closes = _closes_for(sessions) if session_closes is None else session_closes
    with (
        patch(
            "manta_trading.data.gaps.minute_coverage.fetch_terminal_rows",
            return_value=terminal_rows or [],
        ),
        patch(
            "manta_trading.data.gaps.minute_coverage.clamp_to_lifecycle",
            return_value=(lifecycle_from, lifecycle_to),
        ),
        patch(
            "manta_trading.data.gaps.minute_coverage.fetch_sessions",
            return_value=sessions,
        ),
        patch(
            "manta_trading.data.gaps.minute_coverage.fetch_session_bounds",
            return_value=closes,
        ),
    ):
        return compute_missing_minute_sessions(
            conn,
            symbol,
            coverage_index,
            lifecycle_from,
            lifecycle_to,
            uncovered_days=uncovered_days,
        )


class TestComputeMissingMinuteSessions:
    def test_past_hole_seeds_only_the_hole(self) -> None:
        s1, s2, s3, s4 = (
            _dt(2024, 1, 2),
            _dt(2024, 1, 3),
            _dt(2024, 1, 4),
            _dt(2024, 1, 5),
        )
        sessions = [s1, s2, s3, s4]
        # s1, s4 covered; s2, s3 (interior hole) missing
        coverage_index = {"AAPL": {s1.date(), s4.date()}}
        result = _patched_run(
            lifecycle_from=s1,
            lifecycle_to=s4,
            sessions=sessions,
            coverage_index=coverage_index,
        )
        assert len(result) == 1
        assert result[0].gap_start_utc == s2
        # Slice 921: the range ends at the last missing session's CLOSE.
        assert result[0].gap_end_utc == s3 + _REGULAR_SPAN

    def test_fully_covered_returns_empty(self) -> None:
        sessions = [_dt(2024, 1, 2), _dt(2024, 1, 3)]
        coverage_index = {"AAPL": {s.date() for s in sessions}}
        result = _patched_run(
            lifecycle_from=sessions[0],
            lifecycle_to=sessions[-1],
            sessions=sessions,
            coverage_index=coverage_index,
        )
        assert result == []

    def test_empty_symbol_spans_full_history(self) -> None:
        sessions = [_dt(2024, 1, 2), _dt(2024, 1, 3), _dt(2024, 1, 4)]
        coverage_index: dict[str, set[date]] = {}
        result = _patched_run(
            lifecycle_from=sessions[0],
            lifecycle_to=sessions[-1],
            sessions=sessions,
            coverage_index=coverage_index,
        )
        assert len(result) == 1
        assert result[0].gap_start_utc == sessions[0]
        assert result[0].gap_end_utc == sessions[-1] + _REGULAR_SPAN

    def test_delisted_clamp_limits_sessions(self) -> None:
        # clamp_to_lifecycle is patched directly, so the delisted clamp is
        # exercised by only returning sessions up to the (patched) clamped
        # window — sessions past delisting never appear in `sessions`.
        sessions = [_dt(2024, 1, 2), _dt(2024, 1, 3)]
        coverage_index: dict[str, set[date]] = {}
        result = _patched_run(
            lifecycle_from=sessions[0],
            lifecycle_to=sessions[-1],
            sessions=sessions,
            coverage_index=coverage_index,
        )
        assert result[-1].gap_end_utc == sessions[-1] + _REGULAR_SPAN

    def test_no_lifecycle_anchor_returns_empty(self) -> None:
        conn = MagicMock()
        with (
            patch(
                "manta_trading.data.gaps.minute_coverage.clamp_to_lifecycle",
                return_value=(None, None),
            ),
            patch(
                "manta_trading.data.gaps.minute_coverage.fetch_sessions",
                return_value=[],
            ),
        ):
            result = compute_missing_minute_sessions(
                conn, "AAPL", {}, _dt(2024, 1, 1), _dt(2024, 1, 31)
            )
        assert result == []

    def test_no_sessions_returns_empty(self) -> None:
        result = _patched_run(
            lifecycle_from=_dt(2024, 1, 1),
            lifecycle_to=_dt(2024, 1, 31),
            sessions=[],
            coverage_index={},
        )
        assert result == []


class TestCoverageIndexIntegration:
    """End-to-end: build_minute_coverage_index's real (datetime-typed) rows
    feed correctly into compute_missing_minute_sessions' day-set diff.

    Regression for a production bug (slice 162 walkthrough, 2026-07-17):
    date_trunc('day', ...) returns a timestamptz, so build_minute_coverage_index
    stored datetime keys while the diff checked `session.date()` (a plain
    date) — the two never matched, so every symbol appeared fully uncovered
    and seeded a single full-history span regardless of real coverage.
    """

    @pytest.fixture(autouse=True)
    def _guard_passes(self):
        with patch(
            "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
            return_value=_fresh_verdict(),
        ):
            yield

    def test_fully_covered_symbol_from_real_cagg_rows_seeds_nothing(self) -> None:
        sessions = [_dt(2024, 1, 2), _dt(2024, 1, 3), _dt(2024, 1, 4)]
        # Simulates raw psycopg rows from `date_trunc('day', time_bucket)` —
        # datetime, not date.
        cagg_rows = [
            ("TSLA", datetime(2024, 1, 2, tzinfo=UTC)),
            ("TSLA", datetime(2024, 1, 3, tzinfo=UTC)),
            ("TSLA", datetime(2024, 1, 4, tzinfo=UTC)),
        ]
        index_conn = _make_index_conn(cagg_rows)
        coverage_index = build_minute_coverage_index(index_conn)
        assert coverage_index is not None

        result = _patched_run(
            lifecycle_from=sessions[0],
            lifecycle_to=sessions[-1],
            sessions=sessions,
            coverage_index=coverage_index,
            symbol="TSLA",
        )
        assert result == []


# ---------------------------------------------------------------------------
# Slice 168 — freshness guard wiring (task 7.2)
# ---------------------------------------------------------------------------


class TestCoverageFreshnessGuard:
    """The guard runs before the coverage query and refuses on a stale cagg."""

    def test_stale_cagg_returns_none_without_running_the_coverage_query(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        conn = _make_index_conn([("AAPL", datetime(2024, 1, 2, tzinfo=UTC))])
        cur = conn.cursor.return_value
        with (
            patch(
                "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
                return_value=_stale_verdict(),
            ),
            caplog.at_level(logging.ERROR),
        ):
            result = build_minute_coverage_index(conn)

        assert result is None, "a stale source cagg must not produce an index"
        executed = " ".join(
            call.args[0] for call in cur.execute.call_args_list if call.args
        )
        assert "GROUP BY" not in executed, (
            "the coverage query must not run once the guard has tripped"
        )
        # The ERROR must name the cagg, the measured lag, and the signals so an
        # operator can act on it without reading the code.
        assert "minute_4hour_ohlcv" in caplog.text
        assert "4 days" in caplog.text
        assert StalenessSignal.NOT_SCHEDULED.value in caplog.text

    def test_stale_cagg_never_falls_back_to_a_full_window_seed(self) -> None:
        # None means "index unavailable, skip coverage-aware seeding" — it must
        # never degrade into an empty dict, which reads as "nothing covered"
        # and would re-seed 22 years for every symbol.
        conn = _make_index_conn([])
        with patch(
            "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
            return_value=_stale_verdict(),
        ):
            assert build_minute_coverage_index(conn) is None

    def test_guard_is_asserted_against_the_h4_cagg(self) -> None:
        conn = _make_index_conn([])
        with patch(
            "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
            return_value=_fresh_verdict(),
        ) as guard:
            build_minute_coverage_index(conn)
        assert guard.call_args.args[1] == GRANULARITY_SOURCE[Granularity.H4]

    def test_fresh_cagg_leaves_existing_behavior_unchanged(self) -> None:
        rows = [
            ("AAPL", datetime(2024, 1, 2, tzinfo=UTC)),
            ("MSFT", datetime(2024, 1, 2, tzinfo=UTC)),
        ]
        conn = _make_index_conn(rows)
        with patch(
            "manta_trading.data.gaps.minute_coverage.assert_cagg_fresh",
            return_value=_fresh_verdict(),
        ):
            result = build_minute_coverage_index(conn)
        assert result == {
            "AAPL": {date(2024, 1, 2)},
            "MSFT": {date(2024, 1, 2)},
        }


# ---------------------------------------------------------------------------
# Slice 921 — the minute range ends at the session close (Task 1.3, SC1)
# ---------------------------------------------------------------------------


class TestMinuteRangeEndsAtSessionClose:
    """The range end is the last missing session's ``session_close_utc``.

    Root cause this covers: ``group_sessions_into_ranges`` ends every range at
    the last missing session's OPEN, and ``_do_minute_symbol`` passes that as
    EODHD's ``to``. EODHD honors ``to`` exactly, so a trailing session was
    fetched as a one-minute window and returned a single bar — the 70,298
    UNKNOWN / 42,775 zero-width rows measured on 2026-09-07.

    Fixtures use real ``trading_sessions`` values in BOTH DST regimes —
    13:30/20:00 UTC in summer (EDT) and 14:30/21:00 UTC in winter (EST). A
    single-regime fixture cannot distinguish a close that is read from one
    computed as the open plus a fixed offset.
    """

    # Summer (EDT): 09:30–16:00 ET == 13:30–20:00 UTC.
    _SUMMER_OPEN = 13
    _SUMMER_CLOSE = 20
    # Winter (EST): 09:30–16:00 ET == 14:30–21:00 UTC.
    _WINTER_OPEN = 14
    _WINTER_CLOSE = 21

    @staticmethod
    def _summer(day: int) -> tuple[datetime, datetime]:
        return (
            datetime(2026, 7, day, 13, 30, tzinfo=UTC),
            datetime(2026, 7, day, 20, 0, tzinfo=UTC),
        )

    @staticmethod
    def _winter(day: int) -> tuple[datetime, datetime]:
        return (
            datetime(2026, 1, day, 14, 30, tzinfo=UTC),
            datetime(2026, 1, day, 21, 0, tzinfo=UTC),
        )

    def test_single_missing_summer_session_spans_open_to_close(self) -> None:
        open_utc, close_utc = self._summer(15)
        result = _patched_run(
            lifecycle_from=open_utc,
            lifecycle_to=close_utc,
            sessions=[open_utc],
            coverage_index={},
            session_closes={open_utc: close_utc},
        )
        assert len(result) == 1
        assert result[0].gap_start_utc == open_utc
        # Asserted against the fixture's close value, not open + an offset.
        assert result[0].gap_end_utc == close_utc
        assert result[0].gap_end_utc.hour == self._SUMMER_CLOSE

    def test_single_missing_winter_session_spans_open_to_close(self) -> None:
        open_utc, close_utc = self._winter(15)
        result = _patched_run(
            lifecycle_from=open_utc,
            lifecycle_to=close_utc,
            sessions=[open_utc],
            coverage_index={},
            session_closes={open_utc: close_utc},
        )
        assert len(result) == 1
        assert result[0].gap_start_utc == open_utc
        assert result[0].gap_end_utc == close_utc
        assert result[0].gap_end_utc.hour == self._WINTER_CLOSE
        assert result[0].gap_start_utc.hour == self._WINTER_OPEN

    def test_contiguous_run_spans_first_open_to_last_close(self) -> None:
        bounds = [self._summer(day) for day in (13, 14, 15)]
        sessions = [open_utc for open_utc, _ in bounds]
        result = _patched_run(
            lifecycle_from=sessions[0],
            lifecycle_to=bounds[-1][1],
            sessions=sessions,
            coverage_index={},
            session_closes=dict(bounds),
        )
        assert len(result) == 1
        assert result[0].gap_start_utc == bounds[0][0]
        assert result[0].gap_end_utc == bounds[-1][1]

    def test_two_runs_separated_by_a_covered_day_each_end_at_their_close(
        self,
    ) -> None:
        bounds = [self._winter(12), self._winter(13), self._winter(14)]
        sessions = [open_utc for open_utc, _ in bounds]
        # The middle session is covered, splitting the run in two.
        coverage_index = {"AAPL": {sessions[1].date()}}
        result = _patched_run(
            lifecycle_from=sessions[0],
            lifecycle_to=bounds[-1][1],
            sessions=sessions,
            coverage_index=coverage_index,
            session_closes=dict(bounds),
        )
        assert len(result) == 2
        assert (result[0].gap_start_utc, result[0].gap_end_utc) == bounds[0]
        assert (result[1].gap_start_utc, result[1].gap_end_utc) == bounds[2]

    def test_early_close_session_ends_at_its_real_close(self) -> None:
        # 2026-07-03 half day: 09:30–13:00 ET == 13:30–17:00 UTC.
        open_utc = datetime(2026, 7, 3, 13, 30, tzinfo=UTC)
        early_close = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
        result = _patched_run(
            lifecycle_from=open_utc,
            lifecycle_to=early_close,
            sessions=[open_utc],
            coverage_index={},
            session_closes={open_utc: early_close},
        )
        assert len(result) == 1
        assert result[0].gap_end_utc == early_close
        regular_close = open_utc + _REGULAR_SPAN
        assert result[0].gap_end_utc != regular_close, (
            "an early close proves the close is read, not open + a fixed span"
        )

    def test_missing_close_logs_error_and_drops_the_range(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A NULL session_close_utc is dropped from fetch_session_bounds, so the
        # open has no close. Keeping the open would silently reintroduce the
        # one-minute provider window this slice exists to remove.
        open_utc, _ = self._summer(15)
        with caplog.at_level(logging.ERROR):
            result = _patched_run(
                lifecycle_from=open_utc,
                lifecycle_to=open_utc,
                sessions=[open_utc],
                coverage_index={},
                session_closes={},
            )
        assert result == []
        assert "session_close_utc" in caplog.text
        assert "AAPL" in caplog.text

    def test_one_run_without_a_close_does_not_drop_the_others(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bounds = [self._winter(12), self._winter(13), self._winter(14)]
        sessions = [open_utc for open_utc, _ in bounds]
        coverage_index = {"AAPL": {sessions[1].date()}}
        # The FIRST run's close is missing; the second must still be returned.
        with caplog.at_level(logging.ERROR):
            result = _patched_run(
                lifecycle_from=sessions[0],
                lifecycle_to=bounds[-1][1],
                sessions=sessions,
                coverage_index=coverage_index,
                session_closes={bounds[2][0]: bounds[2][1]},
            )
        assert len(result) == 1
        assert (result[0].gap_start_utc, result[0].gap_end_utc) == bounds[2]


class TestFetchSessionBounds:
    """``fetch_session_bounds`` is the query that supplies those closes."""

    @staticmethod
    def _conn(rows: Sequence[tuple[datetime, datetime | None]]) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall = MagicMock(return_value=rows)
        conn.cursor = MagicMock(return_value=cur)
        return conn

    def test_returns_open_to_close_mapping(self) -> None:
        rows = [
            (
                datetime(2026, 7, 15, 13, 30, tzinfo=UTC),
                datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
            ),
            (
                datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
                datetime(2026, 1, 15, 21, 0, tzinfo=UTC),
            ),
        ]
        result = fetch_session_bounds(
            self._conn(rows),
            "AAPL",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 12, 31, tzinfo=UTC),
        )
        assert result == dict(rows)

    def test_null_close_is_omitted_rather_than_defaulted(self) -> None:
        open_utc = datetime(2026, 7, 15, 13, 30, tzinfo=UTC)
        result = fetch_session_bounds(
            self._conn([(open_utc, None)]),
            "AAPL",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 12, 31, tzinfo=UTC),
        )
        assert result == {}, "a null close must not become a usable range end"

    def test_symbol_and_window_are_bound_parameters(self) -> None:
        conn = self._conn([])
        from_ts = datetime(2026, 1, 1, tzinfo=UTC)
        to_ts = datetime(2026, 12, 31, tzinfo=UTC)
        fetch_session_bounds(conn, "AAPL", from_ts, to_ts)
        sql, params = conn.cursor.return_value.execute.call_args.args
        assert params == ("AAPL", from_ts, to_ts)
        assert "AAPL" not in sql, "the symbol must never be interpolated"
        assert "session_close_utc" in sql


# ---------------------------------------------------------------------------
# Slice 921 Task 6.2 — days forced uncovered for the repair
# ---------------------------------------------------------------------------


class TestUncoveredDaysOverride:
    """The repair must be able to re-seed a day the coverage index calls
    covered.

    The index is built from the coarse cagg, which reports a day as covered
    when it holds ANY bar. A session truncated to its single opening bar —
    the 2026-07-16 onward failure this slice repairs — therefore reads as
    fully covered and would never be re-fetched. Passing the day as uncovered
    is how the repair reaches it.
    """

    def test_a_covered_day_passed_as_uncovered_is_seeded(self) -> None:
        opens = [_dt(2026, 7, 20), _dt(2026, 7, 21), _dt(2026, 7, 22)]
        # Every day is covered as far as the cagg is concerned.
        coverage_index = {"AAPL": {o.date() for o in opens}}
        result = _patched_run(
            lifecycle_from=opens[0],
            lifecycle_to=opens[-1] + timedelta(hours=7),
            sessions=opens,
            coverage_index=coverage_index,
            uncovered_days={opens[1].date()},
        )
        assert len(result) == 1
        assert result[0].gap_start_utc == opens[1]
        assert result[0].gap_end_utc == opens[1] + _REGULAR_SPAN

    def test_several_uncovered_days_group_into_contiguous_ranges(self) -> None:
        opens = [_dt(2026, 7, day) for day in (20, 21, 22, 23)]
        coverage_index = {"AAPL": {o.date() for o in opens}}
        result = _patched_run(
            lifecycle_from=opens[0],
            lifecycle_to=opens[-1] + timedelta(hours=7),
            sessions=opens,
            coverage_index=coverage_index,
            uncovered_days={opens[1].date(), opens[2].date()},
        )
        assert len(result) == 1
        assert result[0].gap_start_utc == opens[1]
        assert result[0].gap_end_utc == opens[2] + _REGULAR_SPAN

    def test_omitting_the_parameter_is_unchanged(self) -> None:
        """Every pre-921 caller passes nothing and must behave as before."""
        opens = [_dt(2026, 7, 20), _dt(2026, 7, 21)]
        coverage_index = {"AAPL": {o.date() for o in opens}}
        assert (
            _patched_run(
                lifecycle_from=opens[0],
                lifecycle_to=opens[-1] + timedelta(hours=7),
                sessions=opens,
                coverage_index=coverage_index,
            )
            == []
        )

    def test_an_empty_set_is_treated_as_no_override(self) -> None:
        opens = [_dt(2026, 7, 20), _dt(2026, 7, 21)]
        coverage_index = {"AAPL": {o.date() for o in opens}}
        assert (
            _patched_run(
                lifecycle_from=opens[0],
                lifecycle_to=opens[-1] + timedelta(hours=7),
                sessions=opens,
                coverage_index=coverage_index,
                uncovered_days=set(),
            )
            == []
        )

    def test_an_uncovered_day_with_no_session_changes_nothing(self) -> None:
        """A day the calendar has no session for cannot be seeded — the diff
        runs over sessions, not over dates."""
        opens = [_dt(2026, 7, 20), _dt(2026, 7, 21)]
        coverage_index = {"AAPL": {o.date() for o in opens}}
        result = _patched_run(
            lifecycle_from=opens[0],
            lifecycle_to=opens[-1] + timedelta(hours=7),
            sessions=opens,
            coverage_index=coverage_index,
            uncovered_days={date(2026, 7, 25)},  # a Saturday, no session
        )
        assert result == []

    def test_it_does_not_mutate_the_caller_s_coverage_index(self) -> None:
        """The index is shared across every symbol in a cycle; subtracting in
        place would silently un-cover that day for everyone after it."""
        opens = [_dt(2026, 7, 20), _dt(2026, 7, 21)]
        covered = {o.date() for o in opens}
        coverage_index = {"AAPL": covered}
        _patched_run(
            lifecycle_from=opens[0],
            lifecycle_to=opens[-1] + timedelta(hours=7),
            sessions=opens,
            coverage_index=coverage_index,
            uncovered_days={opens[0].date()},
        )
        assert coverage_index["AAPL"] == covered


class TestTerminalRowsAreNotReseeded:
    """#22: a session a PROVIDER_HOLE / RETRY_EXHAUSTED row covers has been
    judged; seeding it again re-asks the provider on every walk."""

    def test_a_terminal_covered_session_is_skipped(self) -> None:
        opens = [_dt(2026, 9, 1), _dt(2026, 9, 2), _dt(2026, 9, 3)]
        closes = _closes_for(opens)
        result = _patched_run(
            lifecycle_from=opens[0],
            lifecycle_to=closes[opens[-1]],
            sessions=opens,
            coverage_index={"AAPL": set()},
            terminal_rows=[(opens[1], closes[opens[1]])],
        )
        seeded = {r.gap_start_utc for r in result}
        assert opens[1] not in seeded
        assert opens[0] in seeded and opens[2] in seeded

    def test_a_terminal_row_that_only_overlaps_does_not_cover(self) -> None:
        opens = [_dt(2026, 9, 1), _dt(2026, 9, 2)]
        closes = _closes_for(opens)
        # Ends an hour before the close: the session is not judged, seed it.
        partial = (opens[1], closes[opens[1]] - timedelta(hours=1))
        result = _patched_run(
            lifecycle_from=opens[0],
            lifecycle_to=closes[opens[-1]],
            sessions=opens,
            coverage_index={"AAPL": set()},
            terminal_rows=[partial],
        )
        assert any(r.gap_start_utc <= opens[1] <= r.gap_end_utc for r in result)
