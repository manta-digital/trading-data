"""Unit tests for compute_missing_ranges.

Uses a mocked psycopg connection; no live DB required.

The mock returns controlled lifecycle data, sessions, and stored bars so
each fixture drives a specific branch of the algorithm.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from manta_trading.data.gaps.compute_missing_ranges import (
    GapRange,
    compute_missing_ranges,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _dt(y: int, m: int, d: int, h: int = 14, mi: int = 30) -> datetime:
    """Return a UTC datetime representing a typical market open."""
    return datetime(y, m, d, h, mi, 0, tzinfo=UTC)


def _make_conn(
    *,
    first_listing_date: date | None = date(2020, 1, 1),
    first_data_date: date | None = None,
    delisted_date: date | None = None,
    sessions: list[datetime] | None = None,
    stored: list[datetime] | None = None,
) -> MagicMock:
    """Build a mock psycopg connection returning the given fixture data."""
    conn = MagicMock()

    lifecycle_row = (first_listing_date, first_data_date, delisted_date)
    session_rows = [(s,) for s in (sessions or [])]
    stored_rows = [(s,) for s in (stored or [])]

    call_seq = iter([lifecycle_row, session_rows, stored_rows])

    def _make_cur() -> MagicMock:
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)

        responses = [lifecycle_row, session_rows, stored_rows]
        response_iter = iter(responses)

        def _execute(sql: str, params: Any = None) -> None:
            cur._last_sql = sql

        def _fetchone() -> tuple | None:
            return lifecycle_row

        def _fetchall() -> list[tuple]:
            try:
                return next(response_iter)
            except StopIteration:
                return []

        cur.execute = MagicMock(side_effect=_execute)
        cur.fetchone = MagicMock(side_effect=_fetchone)
        cur.fetchall = MagicMock(side_effect=_fetchall)
        cur.description = [MagicMock(name="mock_col")]
        return cur

    # Each conn.cursor() call returns a fresh cursor mock that advances
    # through the fixture sequence in order.
    cursors = [_make_cur() for _ in range(10)]
    cursor_iter = iter(cursors)

    def _cursor() -> MagicMock:
        return next(cursor_iter)

    conn.cursor = MagicMock(side_effect=_cursor)
    return conn


# ---------------------------------------------------------------------------
# Alternative simpler approach: patch the internal helpers
# ---------------------------------------------------------------------------


def _mock_conn_simple() -> MagicMock:
    """A minimal conn mock that won't be called directly (helpers are patched)."""
    return MagicMock()


class TestComputeMissingRanges:
    """Test compute_missing_ranges by patching internal helper functions."""

    def _run(
        self,
        *,
        lifecycle: tuple = (date(2020, 1, 1), None, None),
        sessions: list[datetime] | None = None,
        stored: set[datetime] | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[GapRange]:
        from_ts = from_ts or _dt(2020, 1, 1)
        to_ts = to_ts or _dt(2024, 12, 31)
        conn = _mock_conn_simple()

        with (
            patch(
                "manta_trading.data.gaps.compute_missing_ranges._clamp_to_lifecycle",
                return_value=(from_ts, to_ts) if lifecycle[0] or lifecycle[1] else (None, None),
            ),
            patch(
                "manta_trading.data.gaps.compute_missing_ranges._fetch_sessions",
                return_value=sessions or [],
            ),
            patch(
                "manta_trading.data.gaps.compute_missing_ranges._fetch_stored_timestamps",
                return_value=stored or set(),
            ),
        ):
            return compute_missing_ranges(conn, "AAPL", "daily", from_ts, to_ts)

    def test_no_lifecycle_anchor_returns_empty(self) -> None:
        result = self._run(lifecycle=(None, None, None))
        assert result == []

    def test_fully_covered_range_returns_empty(self) -> None:
        sessions = [_dt(2024, 1, 2), _dt(2024, 1, 3), _dt(2024, 1, 4)]
        result = self._run(sessions=sessions, stored=set(sessions))
        assert result == []

    def test_no_sessions_returns_empty(self) -> None:
        result = self._run(sessions=[], stored=set())
        assert result == []

    def test_single_day_gap(self) -> None:
        sessions = [_dt(2024, 1, 2), _dt(2024, 1, 3), _dt(2024, 1, 4)]
        # Jan 3 is missing
        stored = {_dt(2024, 1, 2), _dt(2024, 1, 4)}
        result = self._run(sessions=sessions, stored=stored)
        assert len(result) == 1
        assert result[0].gap_start_utc == _dt(2024, 1, 3)
        assert result[0].gap_end_utc == _dt(2024, 1, 3)

    def test_friday_through_monday_gap_is_one_range(self) -> None:
        """Fri + Mon missing with no weekend sessions → single contiguous run."""
        fri = _dt(2024, 11, 22)
        mon = _dt(2024, 11, 25)
        # Sessions list has no weekend entries
        sessions = [fri, mon]
        stored: set[datetime] = set()
        result = self._run(sessions=sessions, stored=stored)
        assert len(result) == 1
        assert result[0].gap_start_utc == fri
        assert result[0].gap_end_utc == mon

    def test_mid_range_hole(self) -> None:
        """A hole in the middle produces one gap, not two."""
        s1 = _dt(2024, 1, 2)
        s2 = _dt(2024, 1, 3)
        s3 = _dt(2024, 1, 4)
        s4 = _dt(2024, 1, 5)
        sessions = [s1, s2, s3, s4]
        # s2 and s3 missing
        stored = {s1, s4}
        result = self._run(sessions=sessions, stored=stored)
        assert len(result) == 1
        assert result[0].gap_start_utc == s2
        assert result[0].gap_end_utc == s3

    def test_multi_week_scattered_gaps(self) -> None:
        """Three separate gaps produce three GapRange objects."""
        s = [_dt(2024, 1, i) for i in range(2, 11)]  # 2..10
        # Missing: 3, 6, 9 (non-adjacent)
        stored = {ts for ts in s if ts.day not in (3, 6, 9)}
        result = self._run(sessions=s, stored=stored)
        assert len(result) == 3

    def test_minute_granularity_accepted(self) -> None:
        """Minute granularity is handled without error."""
        sessions = [_dt(2024, 1, 2)]
        conn = _mock_conn_simple()
        from_ts = _dt(2024, 1, 1)
        to_ts = _dt(2024, 1, 31)

        with (
            patch(
                "manta_trading.data.gaps.compute_missing_ranges._clamp_to_lifecycle",
                return_value=(from_ts, to_ts),
            ),
            patch(
                "manta_trading.data.gaps.compute_missing_ranges._fetch_sessions",
                return_value=sessions,
            ),
            patch(
                "manta_trading.data.gaps.compute_missing_ranges._fetch_stored_timestamps",
                return_value=set(),
            ),
        ):
            result = compute_missing_ranges(conn, "AAPL", "minute", from_ts, to_ts)
        assert len(result) == 1
        assert result[0].granularity == "minute"

    def test_invalid_granularity_raises(self) -> None:
        conn = _mock_conn_simple()
        with pytest.raises(ValueError, match="granularity"):
            compute_missing_ranges(conn, "AAPL", "tick", _dt(2024, 1, 1), _dt(2024, 12, 31))

    def test_gap_range_symbol_and_granularity_set(self) -> None:
        sessions = [_dt(2024, 1, 2), _dt(2024, 1, 3)]
        result = self._run(sessions=sessions, stored=set())
        assert result[0].symbol == "AAPL"
        assert result[0].granularity == "daily"
