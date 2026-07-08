"""Unit tests for _advance_minute_gap (P09).

Verifies that the per-chunk gap-row arithmetic shrinks/splits the picked
gap correctly across all outcomes, with no fixed-period assumption.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.acquisition.daemon.minute import _advance_minute_gap
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.actionable_gap_selector import GapRow
from manta_trading.data.quality.fetch_status import FetchStatus

UTC = timezone.utc


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 0, 0, 0, tzinfo=UTC)


def _gap(start: datetime, end: datetime, *, attempt: int = 1, status: str = "UNKNOWN") -> GapRow:
    return GapRow(
        symbol="AAPL",
        granularity="minute",
        gap_start=start,
        gap_end=end,
        fetch_status=status,
        last_attempt_ts=_dt(2026, 5, 1),
        attempt_count=attempt,
    )


class _CapturingCursor:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple]] = []

    def __enter__(self) -> "_CapturingCursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executes.append((sql, params))


def _make_conn() -> tuple[MagicMock, _CapturingCursor]:
    cursor = _CapturingCursor()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _verbs(executes: list[tuple[str, tuple]]) -> list[str]:
    out: list[str] = []
    for sql, _ in executes:
        s = sql.strip().split(None, 1)[0].upper()
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# SUCCESS — chunk covers full gap → DELETE picked row
# ---------------------------------------------------------------------------


def test_success_chunk_covers_full_gap_deletes_row() -> None:
    gap = _gap(_dt(2024, 1, 1), _dt(2024, 5, 1))
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2024, 1, 1),
        chunk_end=_dt(2024, 5, 1),
        outcome=LastAttemptOutcome.SUCCESS,
        fetch_status=None,
    )

    assert _verbs(cur.executes) == ["DELETE"]
    _, params = cur.executes[0]
    assert params == ("AAPL", "minute", _dt(2024, 1, 1), _dt(2024, 5, 1))


def test_success_chunk_extends_below_gap_start_still_deletes() -> None:
    gap = _gap(_dt(2024, 1, 5), _dt(2024, 5, 1))
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2023, 12, 1),  # < gap_start
        chunk_end=_dt(2024, 5, 1),
        outcome=LastAttemptOutcome.SUCCESS,
        fetch_status=None,
    )

    assert _verbs(cur.executes) == ["DELETE"]


# ---------------------------------------------------------------------------
# SUCCESS — tail slice → shrink (DELETE + INSERT older portion)
# ---------------------------------------------------------------------------


def test_success_tail_slice_shrinks_to_older_portion() -> None:
    # 240-month-style gap; chunk is the trailing 120-day slice.
    gap = _gap(_dt(2006, 1, 3), _dt(2026, 5, 6), attempt=3)
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2026, 1, 6),
        chunk_end=_dt(2026, 5, 6),
        outcome=LastAttemptOutcome.SUCCESS,
        fetch_status=None,
    )

    assert _verbs(cur.executes) == ["DELETE", "INSERT"]
    _, del_params = cur.executes[0]
    assert del_params == ("AAPL", "minute", _dt(2006, 1, 3), _dt(2026, 5, 6))

    _, ins_params = cur.executes[1]
    sym, gran, gs, ge, status, last_ts, attempt = ins_params
    assert (sym, gran) == ("AAPL", "minute")
    assert gs == _dt(2006, 1, 3)
    assert ge == _dt(2026, 1, 6)  # shrunk to chunk_start
    assert status == "UNKNOWN"      # original status preserved
    assert attempt == 3             # original attempt count preserved
    assert last_ts == gap.last_attempt_ts


# ---------------------------------------------------------------------------
# PARTIAL — tail slice → DELETE + INSERT older + INSERT chunk
# ---------------------------------------------------------------------------


def test_partial_tail_slice_splits_into_older_and_chunk() -> None:
    gap = _gap(_dt(2006, 1, 3), _dt(2026, 5, 6), attempt=2)
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2026, 1, 6),
        chunk_end=_dt(2026, 5, 6),
        outcome=LastAttemptOutcome.PARTIAL,
        fetch_status=FetchStatus.UNKNOWN,
    )

    assert _verbs(cur.executes) == ["DELETE", "INSERT", "INSERT"]

    _, older = cur.executes[1][1], cur.executes[1][1]
    older_params = cur.executes[1][1]
    assert older_params[2] == _dt(2006, 1, 3)
    assert older_params[3] == _dt(2026, 1, 6)
    assert older_params[4] == "UNKNOWN"  # original status
    assert older_params[6] == 2          # original attempts

    chunk_params = cur.executes[2][1]
    assert chunk_params[2] == _dt(2026, 1, 6)
    assert chunk_params[3] == _dt(2026, 5, 6)
    assert chunk_params[4] == str(FetchStatus.UNKNOWN)
    assert chunk_params[6] == 3  # carried-forward + 1


# ---------------------------------------------------------------------------
# PARTIAL/FAILURE — chunk covers full gap → in-place UPDATE
# ---------------------------------------------------------------------------


def test_partial_full_gap_updates_in_place() -> None:
    gap = _gap(_dt(2024, 1, 1), _dt(2024, 5, 1), attempt=1)
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2024, 1, 1),
        chunk_end=_dt(2024, 5, 1),
        outcome=LastAttemptOutcome.PARTIAL,
        fetch_status=FetchStatus.UNKNOWN,
    )

    assert _verbs(cur.executes) == ["UPDATE"]
    _, params = cur.executes[0]
    new_status, _last, attempts, sym, gran, gs, ge = params
    assert new_status == str(FetchStatus.UNKNOWN)
    assert attempts == 2
    assert (sym, gran, gs, ge) == ("AAPL", "minute", _dt(2024, 1, 1), _dt(2024, 5, 1))


# ---------------------------------------------------------------------------
# EMPTY → PROVIDER_HOLE on chunk portion
# ---------------------------------------------------------------------------


def test_empty_marks_chunk_as_provider_hole() -> None:
    gap = _gap(_dt(2024, 1, 1), _dt(2024, 5, 1))
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2024, 1, 1),
        chunk_end=_dt(2024, 5, 1),
        outcome=LastAttemptOutcome.EMPTY,
        fetch_status=FetchStatus.PROVIDER_HOLE,
    )

    assert _verbs(cur.executes) == ["UPDATE"]
    _, params = cur.executes[0]
    assert params[0] == str(FetchStatus.PROVIDER_HOLE)


# ---------------------------------------------------------------------------
# TRANSIENT_FAILURE → FAILED_RETRYABLE; promotes to RETRY_EXHAUSTED at the cap
# ---------------------------------------------------------------------------


def test_transient_failure_marks_failed_retryable() -> None:
    gap = _gap(_dt(2024, 1, 1), _dt(2024, 5, 1), attempt=1)
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2024, 1, 1),
        chunk_end=_dt(2024, 5, 1),
        outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
        fetch_status=FetchStatus.FAILED_RETRYABLE,
    )

    _, params = cur.executes[0]
    assert params[0] == str(FetchStatus.FAILED_RETRYABLE)
    assert params[2] == 2


def test_failed_retryable_promotes_to_retry_exhausted_at_cap() -> None:
    gap = _gap(_dt(2024, 1, 1), _dt(2024, 5, 1), attempt=MAX_RETRY_COUNT - 1)
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=_dt(2024, 1, 1),
        chunk_end=_dt(2024, 5, 1),
        outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
        fetch_status=FetchStatus.FAILED_RETRYABLE,
    )

    _, params = cur.executes[0]
    assert params[0] == str(FetchStatus.RETRY_EXHAUSTED)
    assert params[2] == MAX_RETRY_COUNT


# ---------------------------------------------------------------------------
# Sanity: helper assumption that chunk_end == gap.gap_end is not enforced,
# but tail-slice path should work for any chunk_start in (gap_start, gap_end].
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days_back", [1, 30, 120, 3650])
def test_success_tail_slice_is_period_agnostic(days_back: int) -> None:
    from datetime import timedelta

    gap_start = _dt(1995, 1, 3)
    gap_end = _dt(2026, 5, 6)
    chunk_start = gap_end - timedelta(days=days_back)
    gap = _gap(gap_start, gap_end)
    conn, cur = _make_conn()

    _advance_minute_gap(
        conn,
        picked=gap,
        chunk_start=chunk_start,
        chunk_end=gap_end,
        outcome=LastAttemptOutcome.SUCCESS,
        fetch_status=None,
    )

    # Always DELETE original; INSERT older portion only when chunk_start > gap_start.
    if chunk_start > gap_start:
        assert _verbs(cur.executes) == ["DELETE", "INSERT"]
        _, ins = cur.executes[1]
        assert ins[3] == chunk_start
    else:
        assert _verbs(cur.executes) == ["DELETE"]
