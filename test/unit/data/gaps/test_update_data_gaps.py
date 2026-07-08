"""Unit tests for update_data_gaps algorithm correctness.

Patches compute_missing_ranges and the advisory_lock so no live DB is needed.
Verifies that the function's DB-mutation behavior matches the spec for all
required fixture cases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.compute_missing_ranges import GapRange
from manta_trading.data.gaps.update_data_gaps import UpdateResult, update_data_gaps
from manta_trading.data.quality.fetch_status import FetchStatus

UTC = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 14, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers for building mock connection
# ---------------------------------------------------------------------------


class _CapturingCursor:
    """Cursor that records execute() calls and returns configurable results."""

    def __init__(self) -> None:
        self.executes: list[tuple] = []
        self._fetchone_val: tuple | None = None
        self._fetchall_val: list[tuple] = []
        self.rowcount: int = 0
        self.description: list = [MagicMock(name="gap_start"), MagicMock(name="gap_end"),
                                   MagicMock(name="fetch_status"), MagicMock(name="attempt_count")]
        # Make description items have .name attr
        self.description[0].name = "gap_start"
        self.description[1].name = "gap_end"
        self.description[2].name = "fetch_status"
        self.description[3].name = "attempt_count"

    def __enter__(self) -> "_CapturingCursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executes.append((sql, params))

    def fetchone(self) -> tuple | None:
        return self._fetchone_val

    def fetchall(self) -> list[tuple]:
        return self._fetchall_val


def _make_conn(prior_rows: list[dict] | None = None) -> tuple[MagicMock, list[_CapturingCursor]]:
    """Return (conn, cursor_list) where cursors track execute calls in order."""
    cursors: list[_CapturingCursor] = [_CapturingCursor() for _ in range(20)]
    cursor_iter = iter(cursors)

    # Configure first cursor to return prior rows for _fetch_prior_rows
    if prior_rows:
        # _fetch_prior_rows is called first; its cursor returns prior_rows
        # The cursor's fetchall is called inside _fetch_prior_rows
        # We need fetchall to return the prior_rows as tuples in order
        first_cur = cursors[0]
        first_cur._fetchall_val = [
            (r["gap_start"], r["gap_end"], r["fetch_status"], r["attempt_count"])
            for r in prior_rows
        ]
        # Also second cursor (for _reset_terminal_rows if needed or _delete_intersecting)
        # subsequent cursors return empty results by default

    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=lambda: next(cursor_iter))
    return conn, cursors


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUpdateDataGaps:
    def _call(
        self,
        *,
        prior_rows: list[dict] | None = None,
        gap_ranges: list[GapRange] | None = None,
        fetch_status: FetchStatus | None = FetchStatus.UNKNOWN,
        outcome: LastAttemptOutcome = LastAttemptOutcome.SUCCESS,
        force_reset_terminal: bool = False,
    ) -> tuple[UpdateResult, list[_CapturingCursor]]:
        conn, cursors = _make_conn(prior_rows or [])
        gap_ranges = gap_ranges or []

        with patch(
            "manta_trading.data.gaps.update_data_gaps.compute_missing_ranges",
            return_value=gap_ranges,
        ):
            result = update_data_gaps(
                conn,
                "AAPL",
                "daily",
                _dt(2024, 1, 1),
                _dt(2024, 12, 31),
                fetch_status,
                force_reset_terminal=force_reset_terminal,
                outcome=outcome,
            )
        return result, cursors

    def test_first_attempt_inserts_with_count_1(self) -> None:
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        result, cursors = self._call(gap_ranges=[gap], fetch_status=FetchStatus.UNKNOWN)

        assert result.gaps_inserted == 1
        assert result.gaps_promoted_exhausted == 0
        # Find the INSERT execute call
        insert_calls = [
            c for cur in cursors for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
            for c in [(sql, params)]
        ]
        assert len(insert_calls) == 1
        _, params = insert_calls[0]
        assert params[6] == 1  # attempt_count = 1

    def test_success_outcome_inserts_no_gap_rows(self) -> None:
        result, _ = self._call(gap_ranges=[], fetch_status=None, outcome=LastAttemptOutcome.SUCCESS)
        assert result.gaps_inserted == 0

    def test_retry_exhausted_promoted_at_max_count(self) -> None:
        # Prior rows have attempt_count = MAX_RETRY_COUNT - 1 so next insert → MAX
        prior = [
            {
                "gap_start": _dt(2024, 1, 2),
                "gap_end": _dt(2024, 1, 5),
                "fetch_status": str(FetchStatus.UNKNOWN),
                "attempt_count": MAX_RETRY_COUNT - 1,
            }
        ]
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        result, cursors = self._call(
            prior_rows=prior,
            gap_ranges=[gap],
            fetch_status=FetchStatus.UNKNOWN,
        )
        assert result.gaps_promoted_exhausted == 1
        insert_calls = [
            (sql, params)
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0][1][4] == str(FetchStatus.RETRY_EXHAUSTED)

    def test_force_reset_terminal_clears_terminal_rows(self) -> None:
        result, cursors = self._call(
            gap_ranges=[],
            fetch_status=None,
            force_reset_terminal=True,
        )
        # Should have a DELETE with IN (...PROVIDER_HOLE, RETRY_EXHAUSTED...)
        delete_calls = [
            (sql, params)
            for cur in cursors
            for sql, params in cur.executes
            if "DELETE FROM data_gaps" in sql and "PROVIDER_HOLE" in str(params)
        ]
        assert len(delete_calls) >= 1

    def test_acquisition_state_upserted(self) -> None:
        result, cursors = self._call(outcome=LastAttemptOutcome.SUCCESS)
        state_calls = [
            (sql, params)
            for cur in cursors
            for sql, params in cur.executes
            if "acquisition_state" in sql and "INSERT" in sql
        ]
        assert len(state_calls) == 1
        assert "success" in str(state_calls[0][1])

    def test_different_fetch_status_resets_carry_forward(self) -> None:
        """A repeat with different fetch_status treats it as first attempt."""
        prior = [
            {
                "gap_start": _dt(2024, 1, 2),
                "gap_end": _dt(2024, 1, 5),
                "fetch_status": str(FetchStatus.FAILED_RETRYABLE),  # different
                "attempt_count": 3,
            }
        ]
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        result, cursors = self._call(
            prior_rows=prior,
            gap_ranges=[gap],
            fetch_status=FetchStatus.UNKNOWN,  # different from prior
        )
        insert_calls = [
            params
            for cur in cursors
            for sql, params in cur.executes
            if "INSERT INTO data_gaps" in sql
        ]
        assert len(insert_calls) == 1
        # attempt_count should be 1 (no carry-forward since status differs)
        assert insert_calls[0][6] == 1

    def test_delete_intersecting_called_before_insert(self) -> None:
        gap = GapRange("AAPL", "daily", _dt(2024, 1, 2), _dt(2024, 1, 5))
        _, cursors = self._call(gap_ranges=[gap])
        all_sqls = [sql for cur in cursors for sql, _ in cur.executes]
        # DELETE should appear before INSERT
        delete_idx = next((i for i, s in enumerate(all_sqls) if "DELETE FROM data_gaps" in s), -1)
        insert_idx = next((i for i, s in enumerate(all_sqls) if "INSERT INTO data_gaps" in s), -1)
        assert delete_idx != -1, "Expected a DELETE"
        assert insert_idx != -1, "Expected an INSERT"
        assert delete_idx < insert_idx
