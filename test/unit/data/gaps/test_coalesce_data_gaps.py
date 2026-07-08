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
    ) -> tuple[int, list[dict]]:
        """Run coalesce and return (merge_count, resulting_rows).

        next_session_map: {date: date | None} mapping gap_end.date() → next_session_date
        """
        conn = _make_conn()
        inserted: list[dict] = []

        def _fake_next_session(
            c: object, cal_id: str, after_date: date
        ) -> date | None:
            return (next_session_map or {}).get(after_date)

        def _fake_insert(c: object, sym: str, gran: str, result_rows: list[dict]) -> None:
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

            count = coalesce_data_gaps(conn, "AAPL", "daily")

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
