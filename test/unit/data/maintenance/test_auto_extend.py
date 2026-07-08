"""Unit tests for auto_extend.maybe_extend_trading_sessions (T3)."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

import manta_trading.data.maintenance.auto_extend as _mod
from manta_trading.data.maintenance.auto_extend import (
    AutoExtendResult,
    maybe_extend_trading_sessions,
)
from manta_trading.constants import TRADING_SESSIONS_HORIZON_WARN_DAYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date.today()
_THRESHOLD = _TODAY + timedelta(days=TRADING_SESSIONS_HORIZON_WARN_DAYS)

_CALENDARS_ROW = {
    "calendar_id": "NYSE",
    "timezone": "America/New_York",
    "market_open": datetime.strptime("09:30", "%H:%M").time(),
    "market_close": datetime.strptime("16:00", "%H:%M").time(),
}

_HOLIDAYS_ROW = {
    "holiday_date": date(2099, 1, 1),
    "market_status": "CLOSED",
    "early_close_time": None,
    "late_open_time": None,
}


def _make_conn_factory(
    calendars: list[dict],
    max_date: date | None,
    holidays: list[dict] | None = None,
) -> MagicMock:
    """Build a conn_factory mock with controllable query returns.

    The mock uses a context manager protocol so ``with conn_factory() as conn``
    works correctly.
    """
    if holidays is None:
        holidays = [_HOLIDAYS_ROW]

    conn = MagicMock()
    cursor_cm = MagicMock()
    cur = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor_cm

    # fetchall side-effects: calendars list, then holidays list, repeated
    cur.fetchall.side_effect = [calendars, holidays] * 20
    # fetchone: returns {"max_date": max_date} for MAX queries
    cur.fetchone.return_value = {"max_date": max_date}
    # rowcount for executemany
    cur.rowcount = 42

    factory = MagicMock()
    factory.return_value.__enter__ = MagicMock(return_value=conn)
    factory.return_value.__exit__ = MagicMock(return_value=False)
    return factory


def _reset_gate() -> None:
    """Reset module-level _last_extend_at to None between tests."""
    _mod._last_extend_at = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_op_when_horizon_healthy() -> None:
    """If MAX(session_date) >= today + 90 days, no populate called."""
    _reset_gate()
    healthy_max = _TODAY + timedelta(days=TRADING_SESSIONS_HORIZON_WARN_DAYS + 30)
    factory = _make_conn_factory([_CALENDARS_ROW], healthy_max)

    with patch(
        "manta_trading.data.maintenance.auto_extend.populate_trading_sessions"
    ) as mock_pop:
        result = maybe_extend_trading_sessions(factory, bypass_gate=True)

    mock_pop.assert_not_called()
    assert result.triggered is False
    assert result.calendars_extended == []


def test_extends_when_horizon_short() -> None:
    """If MAX(session_date) < today + 90 days, populate_trading_sessions called."""
    _reset_gate()
    short_max = _TODAY + timedelta(days=30)
    factory = _make_conn_factory([_CALENDARS_ROW], short_max)

    fake_rows = [
        {
            "calendar_id": "NYSE",
            "session_date": _TODAY + timedelta(days=365 * 2),
            "session_open_utc": datetime.now(timezone.utc),
            "session_close_utc": datetime.now(timezone.utc),
        }
    ]
    with patch(
        "manta_trading.data.maintenance.auto_extend.populate_trading_sessions",
        return_value=fake_rows,
    ):
        result = maybe_extend_trading_sessions(factory, bypass_gate=True)

    assert result.triggered is True
    assert "NYSE" in result.calendars_extended
    assert result.rows_inserted > 0


def test_no_op_when_null_horizon() -> None:
    """NULL max_date (empty table) triggers an extend (treats as horizon=None)."""
    _reset_gate()
    factory = _make_conn_factory([_CALENDARS_ROW], None)

    fake_rows = [
        {
            "calendar_id": "NYSE",
            "session_date": _TODAY + timedelta(days=365 * 2),
            "session_open_utc": datetime.now(timezone.utc),
            "session_close_utc": datetime.now(timezone.utc),
        }
    ]
    with patch(
        "manta_trading.data.maintenance.auto_extend.populate_trading_sessions",
        return_value=fake_rows,
    ):
        result = maybe_extend_trading_sessions(factory, bypass_gate=True)

    assert result.triggered is True
    assert "NYSE" in result.calendars_extended


def test_gate_blocks_second_call() -> None:
    """Second call within 24h returns no-op without touching DB."""
    _reset_gate()
    short_max = _TODAY + timedelta(days=30)
    factory = _make_conn_factory([_CALENDARS_ROW], short_max)

    fake_rows = [
        {
            "calendar_id": "NYSE",
            "session_date": _TODAY + timedelta(days=365 * 2),
            "session_open_utc": datetime.now(timezone.utc),
            "session_close_utc": datetime.now(timezone.utc),
        }
    ]
    with patch(
        "manta_trading.data.maintenance.auto_extend.populate_trading_sessions",
        return_value=fake_rows,
    ):
        # First call — should trigger
        result1 = maybe_extend_trading_sessions(factory, bypass_gate=False)

    factory2 = _make_conn_factory([_CALENDARS_ROW], short_max)
    # Second call — should be gated
    result2 = maybe_extend_trading_sessions(factory2, bypass_gate=False)

    # First call triggered (bypassing gate with first call None → gate unset)
    assert result2.triggered is False
    # Gate was set: factory2 should not have been called
    factory2.assert_not_called()


def test_bypass_gate_ignores_timestamp() -> None:
    """bypass_gate=True runs regardless of _last_extend_at."""
    _mod._last_extend_at = datetime.now()  # simulate recent run
    short_max = _TODAY + timedelta(days=30)
    factory = _make_conn_factory([_CALENDARS_ROW], short_max)

    fake_rows = [
        {
            "calendar_id": "NYSE",
            "session_date": _TODAY + timedelta(days=365 * 2),
            "session_open_utc": datetime.now(timezone.utc),
            "session_close_utc": datetime.now(timezone.utc),
        }
    ]
    with patch(
        "manta_trading.data.maintenance.auto_extend.populate_trading_sessions",
        return_value=fake_rows,
    ):
        result = maybe_extend_trading_sessions(factory, bypass_gate=True)

    # Should have run despite recent _last_extend_at
    assert result.triggered is True
    _reset_gate()


def test_insert_error_continues() -> None:
    """INSERT batch failure → error set, triggered=False, no re-raise."""
    _reset_gate()
    short_max = _TODAY + timedelta(days=30)
    factory = _make_conn_factory([_CALENDARS_ROW], short_max)

    fake_rows = [
        {
            "calendar_id": "NYSE",
            "session_date": _TODAY + timedelta(days=365 * 2),
            "session_open_utc": datetime.now(timezone.utc),
            "session_close_utc": datetime.now(timezone.utc),
        }
    ]

    # Make executemany raise
    conn = MagicMock()
    cursor_cm = MagicMock()
    cur = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = [[_CALENDARS_ROW], [_HOLIDAYS_ROW]] * 10
    cur.fetchone.return_value = {"max_date": short_max}
    cur.executemany.side_effect = Exception("DB exploded")
    conn.cursor.return_value = cursor_cm

    error_factory = MagicMock()
    error_factory.return_value.__enter__ = MagicMock(return_value=conn)
    error_factory.return_value.__exit__ = MagicMock(return_value=False)

    with patch(
        "manta_trading.data.maintenance.auto_extend.populate_trading_sessions",
        return_value=fake_rows,
    ):
        result = maybe_extend_trading_sessions(error_factory, bypass_gate=True)

    assert result.error is not None
    assert result.triggered is False


def test_last_extend_at_not_updated_on_error() -> None:
    """_last_extend_at stays None after an error so next call retries."""
    _reset_gate()
    short_max = _TODAY + timedelta(days=30)

    fake_rows = [
        {
            "calendar_id": "NYSE",
            "session_date": _TODAY + timedelta(days=365 * 2),
            "session_open_utc": datetime.now(timezone.utc),
            "session_close_utc": datetime.now(timezone.utc),
        }
    ]

    conn = MagicMock()
    cursor_cm = MagicMock()
    cur = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    cur.fetchall.side_effect = [[_CALENDARS_ROW], [_HOLIDAYS_ROW]] * 10
    cur.fetchone.return_value = {"max_date": short_max}
    cur.executemany.side_effect = Exception("DB exploded")
    conn.cursor.return_value = cursor_cm

    error_factory = MagicMock()
    error_factory.return_value.__enter__ = MagicMock(return_value=conn)
    error_factory.return_value.__exit__ = MagicMock(return_value=False)

    with patch(
        "manta_trading.data.maintenance.auto_extend.populate_trading_sessions",
        return_value=fake_rows,
    ):
        maybe_extend_trading_sessions(error_factory, bypass_gate=True)

    # Gate must NOT be advanced on error
    assert _mod._last_extend_at is None
