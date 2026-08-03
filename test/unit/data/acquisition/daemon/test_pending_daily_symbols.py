"""Tests for the slice 912 D1 derived daily work list.

``pending_daily_symbols`` is the replacement for the runner's in-memory
once-per-day timer: it derives remaining work from
``acquisition_state.last_attempt_ts`` so an interrupted pass resumes at exactly
the symbols it never reached. These tests exercise the classification against a
mock connection — the SQL itself is exercised by the cycle-level tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import DAILY_CYCLE_START_OFFSET
from manta_trading.data.acquisition.daemon.daily import (
    DailyWorkList,
    daily_pass_boundary,
    pending_daily_symbols,
)

BOUNDARY = datetime(2026, 8, 3, 0, 30, tzinfo=UTC)
BEFORE = BOUNDARY - timedelta(hours=20)
AFTER = BOUNDARY + timedelta(minutes=5)

# Row prefixes: (symbol, is_known, has_calendar). The query reports the two
# unactionable causes separately so the operator is told which one they have.
KNOWN = ("AAPL", True, True)
NO_CAL = ("AAPL", True, False)
UNKNOWN = ("AAPL", False, False)


def _conn_returning(rows: list[tuple]) -> MagicMock:
    """Mock connection whose cursor yields ``rows`` from fetchall()."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


# ---------------------------------------------------------------------------
# Classification — one case per branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected_pending", "expected_no_calendar", "expected_unknown", "why"),
    [
        (KNOWN + (BEFORE,), ["AAPL"], [], [], "attempted before the boundary"),
        (KNOWN + (AFTER,), [], [], [], "attempted after the boundary"),
        (KNOWN + (BOUNDARY,), [], [], [], "attempted exactly at the boundary"),
        (KNOWN + (None,), ["AAPL"], [], [], "NULL last_attempt_ts"),
        (NO_CAL + (None,), [], ["AAPL"], [], "no calendar, never attempted"),
        (NO_CAL + (BEFORE,), [], ["AAPL"], [], "no calendar outranks staleness"),
        (NO_CAL + (AFTER,), [], ["AAPL"], [], "no calendar outranks attempted"),
        (UNKNOWN + (None,), [], [], ["AAPL"], "absent from instruments"),
        (UNKNOWN + (AFTER,), [], [], ["AAPL"], "unknown outranks attempted"),
    ],
)
def test_classification(
    row, expected_pending, expected_no_calendar, expected_unknown, why
):
    conn = _conn_returning([row])
    result = pending_daily_symbols(conn, ["AAPL"], BOUNDARY)
    assert result.pending == expected_pending, why
    assert result.unactionable_no_calendar == expected_no_calendar, why
    assert result.unknown_symbols == expected_unknown, why


def test_absent_acquisition_state_row_is_pending():
    """A symbol with no acquisition_state row LEFT JOINs to NULL and is pending.

    Regression guard for the "never call .date() on None" rule — the old
    once-per-day gate had exactly this trap.
    """
    conn = _conn_returning([("NEWCO", True, True, None)])
    result = pending_daily_symbols(conn, ["NEWCO"], BOUNDARY)
    assert result.pending == ["NEWCO"]


# ---------------------------------------------------------------------------
# Resume semantics — the reason the slice exists
# ---------------------------------------------------------------------------


def test_interrupted_pass_resumes_at_unreached_symbols():
    """A pass that died partway leaves exactly the unreached symbols pending."""
    scope = ["AAPL", "BAC", "CAT", "DE", "EOG"]
    # A pass reached the first three, then died.
    rows = [
        ("AAPL", True, True, AFTER),
        ("BAC", True, True, AFTER),
        ("CAT", True, True, AFTER),
        ("DE", True, True, BEFORE),
        ("EOG", True, True, BEFORE),
    ]
    result = pending_daily_symbols(_conn_returning(rows), scope, BOUNDARY)
    assert result.pending == ["DE", "EOG"]
    assert result.unactionable_no_calendar == []


def test_completed_pass_yields_empty_pending():
    scope = ["AAPL", "BAC", "CAT"]
    rows = [(s, True, True, AFTER) for s in scope]
    result = pending_daily_symbols(_conn_returning(rows), scope, BOUNDARY)
    assert result.pending == []


def test_ordering_is_preserved_not_resorted():
    """Caller order is the processing order (most_stale_first); do not re-sort."""
    scope = ["ZTS", "AAPL", "MSFT"]
    rows = [(s, True, True, BEFORE) for s in scope]
    result = pending_daily_symbols(_conn_returning(rows), scope, BOUNDARY)
    assert result.pending == ["ZTS", "AAPL", "MSFT"]


def test_mixed_scope_partitions_disjointly():
    rows = [
        ("AAPL", True, True, BEFORE),    # pending
        ("NOCAL", True, False, None),    # known, no calendar
        ("GHOST", False, False, None),   # not in instruments at all
        ("BAC", True, True, AFTER),      # done
    ]
    scope = ["AAPL", "NOCAL", "GHOST", "BAC"]
    result = pending_daily_symbols(_conn_returning(rows), scope, BOUNDARY)
    assert result.pending == ["AAPL"]
    assert result.unactionable_no_calendar == ["NOCAL"]
    assert result.unknown_symbols == ["GHOST"]
    buckets = [result.pending, result.unactionable_no_calendar, result.unknown_symbols]
    flat = [s for bucket in buckets for s in bucket]
    assert len(flat) == len(set(flat)), "buckets must be disjoint"


# ---------------------------------------------------------------------------
# Degenerate input
# ---------------------------------------------------------------------------


def test_empty_scope_short_circuits_without_querying():
    conn = MagicMock()
    result = pending_daily_symbols(conn, [], BOUNDARY)
    assert result == DailyWorkList(
        pending=[], unactionable_no_calendar=[], unknown_symbols=[]
    )
    conn.cursor.assert_not_called()


def test_all_unactionable_yields_empty_pending():
    """A scope of only calendar-less symbols must terminate, not spin.

    This is the non-termination D6 exists to prevent: were these left pending,
    the cycle would re-issue the billable bulk EOD call every cadence tick.
    """
    scope = ["NOCAL1", "NOCAL2"]
    rows = [(s, True, False, None) for s in scope]
    result = pending_daily_symbols(_conn_returning(rows), scope, BOUNDARY)
    assert result.pending == []
    assert result.unactionable_no_calendar == scope


# ---------------------------------------------------------------------------
# Pass boundary
# ---------------------------------------------------------------------------


def test_daily_pass_boundary_is_midnight_plus_offset():
    now = datetime(2026, 8, 3, 14, 22, tzinfo=UTC)
    expected = datetime(2026, 8, 3, tzinfo=UTC) + DAILY_CYCLE_START_OFFSET
    assert daily_pass_boundary(now) == expected


def test_daily_pass_boundary_uses_utc_calendar_day():
    """A non-UTC input resolves against its UTC day, not its local one."""
    from datetime import timezone

    # 2026-08-03 21:00 UTC-8 is 2026-08-04 05:00 UTC — the boundary is the 4th.
    now = datetime(2026, 8, 3, 21, 0, tzinfo=timezone(timedelta(hours=-8)))
    expected = datetime(2026, 8, 4, tzinfo=UTC) + DAILY_CYCLE_START_OFFSET
    assert daily_pass_boundary(now) == expected


def test_boundary_before_offset_is_still_todays_pass():
    """At 00:13 UTC the boundary is today's 00:30 — a future instant.

    Nothing can have been attempted at/after it, so every symbol reads pending.
    That is correct: the cycle has not started, and the runner's cadence gate
    (not this function) is what holds it until 00:30.
    """
    now = datetime(2026, 8, 3, 0, 13, tzinfo=UTC)
    boundary = daily_pass_boundary(now)
    assert boundary > now
    rows = [("AAPL", True, True, now - timedelta(hours=1))]
    result = pending_daily_symbols(_conn_returning(rows), ["AAPL"], boundary)
    assert result.pending == ["AAPL"]
