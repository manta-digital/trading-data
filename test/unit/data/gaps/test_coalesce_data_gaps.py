"""Unit tests for coalesce_data_gaps adjacency cases."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.data.gaps.coalesce_data_gaps import coalesce_data_gaps

UTC = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, 0, tzinfo=UTC)


def _row(
    gap_start: datetime,
    gap_end: datetime,
    status: str = "UNKNOWN",
    attempt_count: int = 1,
    last_attempt_ts: datetime | None = None,
) -> dict:
    return {
        "gap_start": gap_start,
        "gap_end": gap_end,
        "fetch_status": status,
        "attempt_count": attempt_count,
        "last_attempt_ts": last_attempt_ts or gap_start,
    }


def _make_conn() -> MagicMock:
    return MagicMock()


class TestCoalesceDataGaps:
    """Patch internal helpers to unit-test the merge logic."""

    def _run(
        self,
        rows: list[dict],
        calendar_id: str = "US",
        next_session_map: dict | None = None,
        granularity: str = "daily",
    ) -> tuple[int, list[dict]]:
        """Run coalesce and return (merge_count, resulting_rows).

        next_session_map: {date: date | None} mapping gap_end.date() → next_session_date
        """
        conn = _make_conn()
        inserted: list[dict] = []

        def _fake_next_session(c: object, cal_id: str, after_date: date) -> date | None:
            return (next_session_map or {}).get(after_date)

        def _fake_insert(
            c: object, sym: str, gran: str, result_rows: list[dict]
        ) -> None:
            inserted.extend(result_rows)

        with (
            patch(
                "manta_trading.data.gaps.coalesce_data_gaps._fetch_rows",
                return_value=rows,
            ),
            patch(
                "manta_trading.data.gaps.coalesce_data_gaps._fetch_calendar_id",
                return_value=calendar_id,
            ),
            patch(
                "manta_trading.data.gaps.coalesce_data_gaps.next_trading_session_after",
                side_effect=_fake_next_session,
            ),
            patch(
                "manta_trading.data.gaps.coalesce_data_gaps._delete_all",
            ),
            patch(
                "manta_trading.data.gaps.coalesce_data_gaps._insert_rows",
                side_effect=_fake_insert,
            ),
        ):
            count = coalesce_data_gaps(conn, "AAPL", granularity)

        return count, inserted

    def test_zero_rows_is_noop(self) -> None:
        count, inserted = self._run([])
        assert count == 0
        assert inserted == []

    def test_one_row_is_noop(self) -> None:
        rows = [_row(_dt(2024, 1, 2), _dt(2024, 1, 2))]
        count, inserted = self._run(rows)
        assert count == 0
        assert inserted == []

    def test_two_adjacent_same_status_merged(self) -> None:
        r1 = _row(_dt(2024, 1, 2), _dt(2024, 1, 2))
        r2 = _row(_dt(2024, 1, 3), _dt(2024, 1, 3))
        # Jan 2 end → Jan 3 is next session
        count, inserted = self._run(
            [r1, r2],
            next_session_map={date(2024, 1, 2): date(2024, 1, 3)},
        )
        assert count == 1
        assert len(inserted) == 1
        assert inserted[0]["gap_start"] == _dt(2024, 1, 2)
        assert inserted[0]["gap_end"] == _dt(2024, 1, 3)

    def test_two_adjacent_different_status_not_merged(self) -> None:
        r1 = _row(_dt(2024, 1, 2), _dt(2024, 1, 2), status="UNKNOWN")
        r2 = _row(_dt(2024, 1, 3), _dt(2024, 1, 3), status="PROVIDER_HOLE")
        count, inserted = self._run(
            [r1, r2],
            next_session_map={date(2024, 1, 2): date(2024, 1, 3)},
        )
        assert count == 0

    def test_two_rows_with_non_trading_day_between_merged(self) -> None:
        """Friday gap_end → Monday gap_start (weekend between) should merge."""
        fri = _dt(2024, 11, 22)
        mon = _dt(2024, 11, 25)
        r1 = _row(fri, fri)
        r2 = _row(mon, mon)
        # next session after Friday is Monday (no Saturday/Sunday)
        count, inserted = self._run(
            [r1, r2],
            next_session_map={date(2024, 11, 22): date(2024, 11, 25)},
        )
        assert count == 1
        assert inserted[0]["gap_end"] == mon

    def test_two_rows_with_trading_day_between_not_merged(self) -> None:
        """Monday gap_end → Wednesday gap_start (Tuesday between) should NOT merge."""
        mon = _dt(2024, 11, 25)
        wed = _dt(2024, 11, 27)
        r1 = _row(mon, mon)
        r2 = _row(wed, wed)
        # next session after Monday is Tuesday (not Wednesday)
        count, inserted = self._run(
            [r1, r2],
            next_session_map={date(2024, 11, 25): date(2024, 11, 26)},
        )
        assert count == 0

    def test_idempotent_re_run(self) -> None:
        """After coalescing, running again returns 0."""
        r1 = _row(_dt(2024, 1, 2), _dt(2024, 1, 3))  # already merged
        count, _ = self._run(
            [r1],
            next_session_map={date(2024, 1, 2): date(2024, 1, 3)},
        )
        assert count == 0

    def test_merge_uses_min_last_attempt_ts(self) -> None:
        earlier = _dt(2024, 1, 1)
        later = _dt(2024, 1, 2)
        r1 = _row(_dt(2024, 1, 2), _dt(2024, 1, 2), last_attempt_ts=later)
        r2 = _row(_dt(2024, 1, 3), _dt(2024, 1, 3), last_attempt_ts=earlier)
        count, inserted = self._run(
            [r1, r2],
            next_session_map={date(2024, 1, 2): date(2024, 1, 3)},
        )
        assert count == 1
        assert inserted[0]["last_attempt_ts"] == earlier

    def test_merge_uses_max_attempt_count(self) -> None:
        r1 = _row(_dt(2024, 1, 2), _dt(2024, 1, 2), attempt_count=2)
        r2 = _row(_dt(2024, 1, 3), _dt(2024, 1, 3), attempt_count=5)
        count, inserted = self._run(
            [r1, r2],
            next_session_map={date(2024, 1, 2): date(2024, 1, 3)},
        )
        assert count == 1
        assert inserted[0]["attempt_count"] == 5


class TestMinuteRowsEndingAtSessionClose:
    """Slice 921 Task 1.6 — the moved minute ``gap_end`` must still coalesce.

    ``_are_adjacent`` compares ``next_trading_session_after(prev.gap_end.date())``
    with ``current.gap_start.date()`` — DATES only. Moving the minute range end
    from the session open (13:30/14:30 UTC) to the session close (20:00/21:00
    UTC) keeps it on the same calendar date, so adjacency is unaffected.

    This is why Decision 1 makes the range end the session close and NOT the
    next UTC midnight: a midnight end would push ``gap_end.date()`` onto the
    following day, ``next_trading_session_after`` would return the session
    after that, and consecutive rows would stop merging — this test fails if
    the ends are moved to midnight.
    """

    def test_consecutive_minute_rows_ending_at_closes_still_coalesce(self) -> None:
        # 2026-01-12 and 2026-01-13, winter regime: 14:30 open, 21:00 close.
        r1 = _row(
            datetime(2026, 1, 12, 14, 30, tzinfo=UTC),
            datetime(2026, 1, 12, 21, 0, tzinfo=UTC),
        )
        r2 = _row(
            datetime(2026, 1, 13, 14, 30, tzinfo=UTC),
            datetime(2026, 1, 13, 21, 0, tzinfo=UTC),
        )
        count, inserted = TestCoalesceDataGaps()._run(
            [r1, r2],
            next_session_map={date(2026, 1, 12): date(2026, 1, 13)},
            granularity="minute",
        )
        assert count == 1
        assert len(inserted) == 1
        assert inserted[0]["gap_start"] == datetime(2026, 1, 12, 14, 30, tzinfo=UTC)
        assert inserted[0]["gap_end"] == datetime(2026, 1, 13, 21, 0, tzinfo=UTC)

    def test_a_midnight_end_would_break_adjacency(self) -> None:
        """Pins the counterfactual named in the class docstring.

        Same two sessions, but with the first row ended at the next UTC
        midnight instead of the close: ``gap_end.date()`` becomes 2026-01-13,
        whose next session is 2026-01-14, which does not match the second
        row's 2026-01-13 start — so the rows do NOT merge.
        """
        r1 = _row(
            datetime(2026, 1, 12, 14, 30, tzinfo=UTC),
            datetime(2026, 1, 13, 0, 0, tzinfo=UTC),
        )
        r2 = _row(
            datetime(2026, 1, 13, 14, 30, tzinfo=UTC),
            datetime(2026, 1, 13, 21, 0, tzinfo=UTC),
        )
        count, inserted = TestCoalesceDataGaps()._run(
            [r1, r2],
            next_session_map={
                date(2026, 1, 12): date(2026, 1, 13),
                date(2026, 1, 13): date(2026, 1, 14),
            },
            granularity="minute",
        )
        assert count == 0
        assert inserted == []
