"""Unit tests for minute_coverage (slice 162 coverage-aware minute seeding).

Uses a mocked psycopg connection; no live DB required. Mocks the DB I/O
boundary (cursor execute/fetchall, patched clamp/session helpers) and tests
the diff/grouping logic with real data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from manta_trading.data.gaps.minute_coverage import (
    build_minute_coverage_index,
    compute_missing_minute_sessions,
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


class TestBuildMinuteCoverageIndex:
    def test_groups_rows_by_symbol(self) -> None:
        rows = [
            ("AAPL", date(2024, 1, 2)),
            ("AAPL", date(2024, 1, 3)),
            ("MSFT", date(2024, 1, 2)),
        ]
        conn = _make_index_conn(rows)
        result = build_minute_coverage_index(conn)
        assert result == {
            "AAPL": {date(2024, 1, 2), date(2024, 1, 3)},
            "MSFT": {date(2024, 1, 2)},
        }

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
        s1, s2, s3, s4 = _dt(2024, 1, 2), _dt(2024, 1, 3), _dt(2024, 1, 4), _dt(2024, 1, 5)
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
