"""Unit tests for pick_most_recent_actionable_gap."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock

import pytest

from manta_trading.data.gaps.actionable_gap_selector import (
    GapRow,
    pick_most_recent_actionable_gap,
)
from manta_trading.data.quality.fetch_status import FetchStatus

UTC = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, 0, tzinfo=UTC)


def _make_conn(return_row: tuple | None) -> MagicMock:
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = return_row
    conn.cursor.return_value = cur
    return conn


class TestPickMostRecentActionableGap:
    def test_returns_none_when_no_rows(self) -> None:
        conn = _make_conn(None)
        result = pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        assert result is None

    def test_returns_gap_row_for_unknown_status(self) -> None:
        row = ("AAPL", "daily", _dt(2024, 6, 1), _dt(2024, 6, 30), "UNKNOWN", None, 1)
        conn = _make_conn(row)
        result = pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        assert result is not None
        assert result.symbol == "AAPL"
        assert result.fetch_status == "UNKNOWN"
        assert result.gap_end == _dt(2024, 6, 30)

    def test_returns_gap_row_for_failed_retryable_status(self) -> None:
        row = (
            "AAPL",
            "daily",
            _dt(2024, 3, 1),
            _dt(2024, 3, 31),
            "FAILED_RETRYABLE",
            _dt(2024, 4, 1),
            2,
        )
        conn = _make_conn(row)
        result = pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        assert result is not None
        assert result.fetch_status == "FAILED_RETRYABLE"
        assert result.attempt_count == 2

    def test_query_filters_to_actionable_statuses_only(self) -> None:
        """SQL IN clause must include UNKNOWN and FAILED_RETRYABLE, not terminal ones."""
        conn = _make_conn(None)
        pick_most_recent_actionable_gap(
            conn, "AAPL", "daily", _dt(2024, 1, 1), _dt(2024, 12, 31)
        )
        cur = conn.cursor.return_value.__enter__.return_value
        call_args = cur.execute.call_args
        sql: str = call_args[0][0]
        params: tuple = call_args[0][1]

        assert "fetch_status = ANY" in sql
        assert "ORDER BY gap_end DESC" in sql
        assert "LIMIT 1" in sql
        # Params should include both actionable statuses
        params_str = str(params)
        assert "UNKNOWN" in params_str
        assert "FAILED_RETRYABLE" in params_str
        assert "PROVIDER_HOLE" not in params_str
        assert "RETRY_EXHAUSTED" not in params_str

    def test_query_scopes_to_window(self) -> None:
        conn = _make_conn(None)
        from_ts = _dt(2024, 1, 1)
        to_ts = _dt(2024, 12, 31)
        pick_most_recent_actionable_gap(conn, "MSFT", "minute", from_ts, to_ts)
        cur = conn.cursor.return_value.__enter__.return_value
        params = cur.execute.call_args[0][1]
        assert "MSFT" in params
        assert "minute" in params
        assert from_ts in params
        assert to_ts in params


class TestSessionCloseEndsAreStillSelected:
    """Slice 921 Task 1.7 — the moved minute ``gap_end`` must still be picked.

    The selector filters ``gap_end <= to_ts``, and the minute daemon passes
    ``to_ts = now_midnight`` (today's UTC midnight). A minute row now ends at
    its session close — 20:00 UTC in summer, 21:00 in winter — on a session
    date strictly before today, so it remains at or below today's midnight and
    is still returned. Only a row ending on today's own date past midnight
    would be excluded, and the minute window never produces one because the
    seed's ``target_end`` is that same midnight.
    """

    def _row(self, gap_end: datetime) -> tuple:
        return (
            "AAPL",
            "minute",
            datetime(gap_end.year, gap_end.month, gap_end.day, 13, 30, tzinfo=UTC),
            gap_end,
            str(FetchStatus.UNKNOWN),
            None,
            1,
        )

    @pytest.mark.parametrize(
        ("label", "gap_end"),
        [
            ("summer close", datetime(2026, 7, 15, 20, 0, tzinfo=UTC)),
            ("winter close", datetime(2026, 1, 15, 21, 0, tzinfo=UTC)),
        ],
    )
    def test_session_close_end_is_returned(self, label: str, gap_end: datetime) -> None:
        conn = _make_conn(self._row(gap_end))
        now_midnight = datetime(2026, 9, 9, tzinfo=UTC)
        result = pick_most_recent_actionable_gap(
            conn, "AAPL", "minute", datetime(2004, 1, 1, tzinfo=UTC), now_midnight
        )
        assert result is not None, label
        assert result.gap_end == gap_end

    def test_window_upper_bound_is_still_now_midnight(self) -> None:
        """The bound the SQL compares against is unchanged by slice 921."""
        conn = _make_conn(None)
        now_midnight = datetime(2026, 9, 9, tzinfo=UTC)
        pick_most_recent_actionable_gap(
            conn, "AAPL", "minute", datetime(2004, 1, 1, tzinfo=UTC), now_midnight
        )
        cur = conn.cursor.return_value.__enter__.return_value
        sql, params = cur.execute.call_args[0]
        assert "gap_end <= %s" in sql
        assert params[-1] == now_midnight
        # A 20:00 close on any date before today satisfies gap_end <= midnight.
        assert datetime(2026, 9, 8, 20, 0, tzinfo=UTC) <= now_midnight


class TestMinGapEndFilter:
    """Slice 921 Task 3.2 — an optional floor on ``gap_end``.

    The trailing phase passes ``now - MINUTE_TRAILING_PRIORITY_WINDOW`` so it
    attempts every symbol's current session before any deep backfill chunk is
    requested. The parameter is additive: every pre-921 caller omits it and
    gets byte-identical SQL.
    """

    _FROM = datetime(2004, 1, 1, tzinfo=UTC)
    _TO = datetime(2026, 9, 9, tzinfo=UTC)

    def test_omitting_the_floor_adds_no_predicate(self) -> None:
        conn = _make_conn(None)
        pick_most_recent_actionable_gap(conn, "AAPL", "minute", self._FROM, self._TO)
        cur = conn.cursor.return_value.__enter__.return_value
        sql, params = cur.execute.call_args[0]
        assert "gap_end >= %s" not in sql
        assert params == ("AAPL", "minute", ANY, self._FROM, self._TO)

    def test_supplying_the_floor_adds_the_predicate_and_binds_it(self) -> None:
        conn = _make_conn(None)
        floor = datetime(2026, 9, 2, tzinfo=UTC)
        pick_most_recent_actionable_gap(
            conn, "AAPL", "minute", self._FROM, self._TO, min_gap_end=floor
        )
        cur = conn.cursor.return_value.__enter__.return_value
        sql, params = cur.execute.call_args[0]
        assert "gap_end >= %s" in sql
        assert params[-1] == floor
        # The floor is a bind parameter, never interpolated into the text.
        assert str(floor) not in sql

    def test_the_floor_does_not_disturb_the_other_predicates(self) -> None:
        conn = _make_conn(None)
        floor = datetime(2026, 9, 2, tzinfo=UTC)
        pick_most_recent_actionable_gap(
            conn, "AAPL", "minute", self._FROM, self._TO, min_gap_end=floor
        )
        cur = conn.cursor.return_value.__enter__.return_value
        sql, _ = cur.execute.call_args[0]
        assert "fetch_status = ANY" in sql
        assert "gap_start >= %s" in sql
        assert "gap_end <= %s" in sql
        assert "ORDER BY gap_end DESC" in sql
        assert "LIMIT 1" in sql

    def test_a_row_inside_the_floor_is_still_returned(self) -> None:
        gap_end = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)
        row = (
            "AAPL",
            "minute",
            datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
            gap_end,
            str(FetchStatus.UNKNOWN),
            None,
            1,
        )
        result = pick_most_recent_actionable_gap(
            _make_conn(row),
            "AAPL",
            "minute",
            self._FROM,
            self._TO,
            min_gap_end=datetime(2026, 9, 2, tzinfo=UTC),
        )
        assert result is not None
        assert result.gap_end == gap_end

    def test_an_explicit_none_floor_behaves_as_omitted(self) -> None:
        """None means "no floor", not "a floor of NULL" — the predicate must
        be absent rather than comparing against NULL, which matches nothing."""
        conn = _make_conn(None)
        pick_most_recent_actionable_gap(
            conn, "AAPL", "minute", self._FROM, self._TO, min_gap_end=None
        )
        cur = conn.cursor.return_value.__enter__.return_value
        sql, params = cur.execute.call_args[0]
        assert "gap_end >= %s" not in sql
        assert len(params) == 5
