"""Unit tests for minute_coverage (slice 162 coverage-aware minute seeding).

Uses a mocked psycopg connection; no live DB required. Mocks the DB I/O
boundary (cursor execute/fetchall, patched clamp/session helpers) and tests
the diff/grouping logic with real data.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from manta_trading.constants import GRANULARITY_SOURCE, Granularity
from manta_trading.data.gaps.minute_coverage import (
    build_minute_coverage_index,
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
# compute_missing_minute_sessions
# ---------------------------------------------------------------------------


def _patched_run(
    *,
    lifecycle_from: datetime,
    lifecycle_to: datetime,
    sessions: list[datetime],
    coverage_index: dict[str, set[date]],
    symbol: str = "AAPL",
):
    conn = MagicMock()
    with (
        patch(
            "manta_trading.data.gaps.minute_coverage.clamp_to_lifecycle",
            return_value=(lifecycle_from, lifecycle_to),
        ),
        patch(
            "manta_trading.data.gaps.minute_coverage.fetch_sessions",
            return_value=sessions,
        ),
    ):
        return compute_missing_minute_sessions(
            conn, symbol, coverage_index, lifecycle_from, lifecycle_to
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
        assert result[0].gap_end_utc == s3

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
        assert result[0].gap_end_utc == sessions[-1]

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
        assert result[-1].gap_end_utc == sessions[-1]

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
