"""Unit tests for auto_extend.maybe_extend_trading_sessions (T3, 221 D6)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import manta_trading.data.maintenance.auto_extend as _mod
from manta_trading.constants import TRADING_SESSIONS_HORIZON_WARN_DAYS
from manta_trading.data.base.session_extension import CalendarExtension
from manta_trading.data.maintenance.auto_extend import maybe_extend_trading_sessions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date.today()
_SHORT_MAX = _TODAY + timedelta(days=30)
_HEALTHY_MAX = _TODAY + timedelta(days=TRADING_SESSIONS_HORIZON_WARN_DAYS + 30)
_EXTENDED_MAX = _TODAY + timedelta(days=365 * 2)
_MOD = "manta_trading.data.maintenance.auto_extend"


def _make_conn_factory(calendar_ids: list[str]) -> MagicMock:
    """conn_factory whose connection lists ``calendar_ids``."""
    cur = MagicMock()
    cur.fetchall.return_value = [(cal,) for cal in calendar_ids]
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cur)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm

    factory = MagicMock()
    factory.return_value.__enter__ = MagicMock(return_value=conn)
    factory.return_value.__exit__ = MagicMock(return_value=False)
    return factory


def _extension(
    *, rows: int = 42, horizon: date = _EXTENDED_MAX, clamped: bool = False
) -> CalendarExtension:
    return CalendarExtension(
        calendar_id="NYSE",
        rows_upserted=rows,
        horizon_after=horizon,
        holidays_seeded_through=horizon,
        clamped_by_holiday_bound=clamped,
    )


@contextmanager
def _patched(
    horizon: date | None, extension: CalendarExtension | Exception | None = None
) -> Iterator[MagicMock]:
    """Patch the horizon read and the shared extension; yield the latter."""
    effect = extension if isinstance(extension, Exception) else None
    with (
        patch(f"{_MOD}.session_horizon", return_value=horizon),
        patch(
            f"{_MOD}.extend_calendar_sessions",
            return_value=extension if effect is None else None,
            side_effect=effect,
        ) as mock_extend,
    ):
        yield mock_extend


def _reset_gate() -> None:
    """Reset module-level _last_extend_at to None between tests."""
    _mod._last_extend_at = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_op_when_horizon_healthy() -> None:
    """If MAX(session_date) >= today + 90 days, the extension is not called."""
    _reset_gate()
    with _patched(_HEALTHY_MAX) as mock_extend:
        result = maybe_extend_trading_sessions(
            _make_conn_factory(["NYSE"]), bypass_gate=True
        )

    mock_extend.assert_not_called()
    assert result.triggered is False
    assert result.calendars_extended == []
    assert result.horizon_after == {"NYSE": _HEALTHY_MAX}


def test_extends_when_horizon_short() -> None:
    """A short horizon extends from MAX(session_date) + 1."""
    _reset_gate()
    with _patched(_SHORT_MAX, _extension()) as mock_extend:
        result = maybe_extend_trading_sessions(
            _make_conn_factory(["NYSE"]), bypass_gate=True
        )

    assert mock_extend.call_args.kwargs["start"] == _SHORT_MAX + timedelta(days=1)
    assert result.triggered is True
    assert result.calendars_extended == ["NYSE"]
    assert result.rows_inserted == 42
    assert result.horizon_after == {"NYSE": _EXTENDED_MAX}
    assert result.clamped == {}


def test_null_horizon_extends_from_current_year() -> None:
    """NULL max_date (empty table) extends from Jan 1 of the current year."""
    _reset_gate()
    with _patched(None, _extension()) as mock_extend:
        result = maybe_extend_trading_sessions(
            _make_conn_factory(["NYSE"]), bypass_gate=True
        )

    assert mock_extend.call_args.kwargs["start"] == date(_TODAY.year, 1, 1)
    assert result.triggered is True


def test_clamped_extension_is_recorded() -> None:
    """A clamp by the holiday bound lands in ``clamped`` even with 0 rows."""
    _reset_gate()
    with _patched(_SHORT_MAX, _extension(rows=0, horizon=_SHORT_MAX, clamped=True)):
        result = maybe_extend_trading_sessions(
            _make_conn_factory(["NYSE"]), bypass_gate=True
        )

    assert result.triggered is False
    assert result.clamped == {"NYSE": _SHORT_MAX}
    assert result.horizon_after == {"NYSE": _SHORT_MAX}


def test_gate_blocks_second_call() -> None:
    """Second call within 24h returns no-op without touching DB."""
    _reset_gate()
    with _patched(_SHORT_MAX, _extension()):
        maybe_extend_trading_sessions(_make_conn_factory(["NYSE"]), bypass_gate=False)

    factory2 = _make_conn_factory(["NYSE"])
    result2 = maybe_extend_trading_sessions(factory2, bypass_gate=False)

    assert result2.triggered is False
    factory2.assert_not_called()


def test_bypass_gate_ignores_timestamp() -> None:
    """bypass_gate=True runs regardless of _last_extend_at."""
    _mod._last_extend_at = _mod.datetime.now()  # simulate recent run
    with _patched(_SHORT_MAX, _extension()):
        result = maybe_extend_trading_sessions(
            _make_conn_factory(["NYSE"]), bypass_gate=True
        )

    assert result.triggered is True
    _reset_gate()


def test_extension_error_continues() -> None:
    """An extension failure sets error, continues, and does not re-raise."""
    _reset_gate()
    with _patched(_SHORT_MAX, RuntimeError("DB exploded")):
        result = maybe_extend_trading_sessions(
            _make_conn_factory(["NYSE", "NASDAQ"]), bypass_gate=True
        )

    assert result.error == "DB exploded"
    assert result.triggered is False


def test_last_extend_at_not_updated_on_error() -> None:
    """_last_extend_at stays None after an error so next call retries."""
    _reset_gate()
    with _patched(_SHORT_MAX, RuntimeError("DB exploded")):
        maybe_extend_trading_sessions(_make_conn_factory(["NYSE"]), bypass_gate=True)

    assert _mod._last_extend_at is None
