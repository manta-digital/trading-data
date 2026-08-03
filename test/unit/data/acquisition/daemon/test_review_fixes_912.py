"""Regression tests for the slice 912 code review (F001-F008).

Each test names the finding it pins. They are grouped here rather than scattered
because what they have in common is provenance: every one of them failed, or
would have failed silently, against the reviewed revision.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from manta_trading.config import Settings
from manta_trading.constants import (
    DAILY_CYCLE_RETRY_INTERVAL,
    CycleGranularity,
)
from manta_trading.data.acquisition.daemon import daily as daily_mod
from manta_trading.data.acquisition.daemon.cadence import (
    daily_pass_boundary,
    utc_day_start,
)
from manta_trading.data.acquisition.daemon.daily import (
    _PENDING_DAILY_SYMBOLS_SQL,
    DailyWorkList,
    pending_daily_symbols,
)
from manta_trading.data.acquisition.daemon.runner import (
    CA_UPDATE_SENTINEL_GRANULARITY,
    RunnerConfig,
    RunnerState,
    daily_cycle_due,
    sleep_until_next_due_event,
)

_MOD = "manta_trading.data.acquisition.daemon.daily"


# ---------------------------------------------------------------------------
# F001 — the STEADY_STATE path must not discard the caller's counts
# ---------------------------------------------------------------------------


class _FakeSettings:
    timescale_db_url = "postgresql://fake/db"
    eodhd_api_key = "fake-key"


def _pool_mock() -> MagicMock:
    pool = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=pool)
    ctx.__exit__ = MagicMock(return_value=False)
    pool.__enter__ = MagicMock(return_value=pool)
    pool.__exit__ = MagicMock(return_value=False)
    conn_ctx = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=MagicMock())
    conn_ctx.__exit__ = MagicMock(return_value=False)
    pool.connection.return_value = conn_ctx
    return pool


def test_steady_state_preserves_unactionable_counts():
    """F001: the dominant production path used to return a fresh report.

    `run_daily_cycle` sets the un-actionable counts, then handed control to
    `_run_steady_state_cycle`, which built its own `CycleReport` — so the counts
    survived on the BACKFILL path and vanished on the STEADY_STATE one. The two
    modes must agree on what a report contains.
    """
    work = DailyWorkList(
        pending=["AAPL"],
        unactionable_no_calendar=["NOCAL1", "NOCAL2"],
        unknown_symbols=["GHOST"],
    )

    def _steady(*, report, symbol_list, **_kwargs):
        # Stand in for the real bulk path: record an outcome, return the report
        # it was given rather than a new one.
        report.success_count = len(symbol_list)
        return report

    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client"),
        patch(f"{_MOD}.pending_daily_symbols", lambda *_a, **_k: work),
        patch(
            f"{_MOD}._select_daily_mode",
            return_value=daily_mod.DailyMode.STEADY_STATE,
        ),
        patch(f"{_MOD}._run_steady_state_cycle", side_effect=_steady),
    ):
        report = daily_mod.run_daily_cycle(
            symbols=["AAPL", "NOCAL1", "NOCAL2", "GHOST"]
        )

    assert report.unactionable_no_calendar == 2, "count discarded by the rebind"
    assert report.unknown_symbols == 1
    assert report.success_count == 1


# ---------------------------------------------------------------------------
# F002 — has_calendar must mean what _last_completed_session means
# ---------------------------------------------------------------------------


def test_work_list_sql_bounds_sessions_to_the_past():
    """F002: a calendar of only future sessions made the work list immortal.

    `_last_completed_session` requires `session_open_utc < NOW()`. Without the
    same bound here, such a symbol is pending, is then skipped for want of a
    fetch window, is never stamped, and returns to pending on every tick — each
    one re-issuing the 100-credit bulk EOD call.
    """
    normalized = " ".join(_PENDING_DAILY_SYMBOLS_SQL.split())
    assert "session_open_utc < NOW()" in normalized


def test_work_list_sql_bounds_the_aggregate_to_scope():
    """F007: the aggregate grouped the whole instrument universe per tick."""
    normalized = " ".join(_PENDING_DAILY_SYMBOLS_SQL.split())
    assert "WHERE i.symbol = ANY(%(symbols)s::text[]) GROUP BY i.symbol" in normalized


# ---------------------------------------------------------------------------
# F004 — one definition of "today's pass begins at"
# ---------------------------------------------------------------------------


def test_gate_and_work_list_share_one_boundary_definition():
    """F004: five copies of the same expression could drift apart.

    The gate deciding a pass may run and the work list deciding what the pass
    already covered have to name the same instant. Asserting they agree at the
    boundary is what the copies could not guarantee.
    """
    boundary = daily_pass_boundary(datetime(2026, 8, 3, 12, 0, tzinfo=UTC))
    state = RunnerState()

    just_before = boundary - timedelta(seconds=1)
    assert daily_cycle_due(state, just_before) is False
    assert daily_cycle_due(state, boundary) is True
    assert daily_pass_boundary(just_before) == boundary


def test_utc_day_start_uses_the_utc_calendar_day():
    """A non-UTC input resolves against its UTC day, not its local one."""
    now = datetime(2026, 8, 3, 21, 0, tzinfo=timezone_minus_8())
    assert utc_day_start(now) == datetime(2026, 8, 4, tzinfo=UTC)


def timezone_minus_8():
    from datetime import timezone

    return timezone(timedelta(hours=-8))


# ---------------------------------------------------------------------------
# F005 — the enum must govern writes, not just reads
# ---------------------------------------------------------------------------


def test_granularity_token_is_derived_from_the_enum():
    """F005: the work-list query reads back exactly what the cycle writes.

    A literal re-typed at a write site drifts silently — the read matches
    nothing, `pending` comes back empty, and the daemon quietly does nothing.
    """
    assert daily_mod._DAILY_GRANULARITY == CycleGranularity.DAILY.value
    assert CA_UPDATE_SENTINEL_GRANULARITY == CycleGranularity.DAILY.value


def test_no_bare_granularity_literals_remain_at_daily_write_sites():
    """The write sites the enum was introduced to protect.

    Matches argument and SQL positions specifically rather than the bare word,
    so prose in a docstring naming the token does not fail the test.
    """
    from pathlib import Path

    source = Path(daily_mod.__file__).read_text(encoding="utf-8")
    assert ', "daily"' not in source, "a granularity literal came back at a call site"
    assert "granularity=\"daily\"" not in source
    assert "granularity = 'daily'" not in source, "SQL literal came back"


def test_runner_config_granularities_default_to_the_enum():
    assert RunnerConfig().granularities == frozenset(CycleGranularity)


# ---------------------------------------------------------------------------
# F008 — the two unactionable causes need different operator actions
# ---------------------------------------------------------------------------


def _conn_returning(rows: list[tuple]) -> MagicMock:
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_unknown_symbol_is_reported_separately_from_missing_calendar():
    """F008: a typo'd --symbols must not be blamed on issue #4."""
    boundary = datetime(2026, 8, 3, 0, 30, tzinfo=UTC)
    rows = [
        ("NOCAL", True, False, None),
        ("AAPLL", False, False, None),
    ]
    work = pending_daily_symbols(_conn_returning(rows), ["NOCAL", "AAPLL"], boundary)

    assert work.unactionable_no_calendar == ["NOCAL"]
    assert work.unknown_symbols == ["AAPLL"]
    assert work.pending == []
    assert set(work.unactionable) == {"NOCAL", "AAPLL"}


def test_warnings_name_the_right_cause(caplog):
    """Each cause gets its own line, and only one of them cites issue #4."""
    work = DailyWorkList(
        pending=[], unactionable_no_calendar=["NOCAL"], unknown_symbols=["AAPLL"]
    )
    with (
        patch(f"{_MOD}.Settings", return_value=_FakeSettings()),
        patch(f"{_MOD}.ConnectionPool", return_value=_pool_mock()),
        patch(f"{_MOD}.httpx.Client"),
        patch(f"{_MOD}.pending_daily_symbols", lambda *_a, **_k: work),
        caplog.at_level(logging.WARNING),
    ):
        report = daily_mod.run_daily_cycle(symbols=["NOCAL", "AAPLL"])

    assert report.nothing_actionable is True
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    calendar_line = next(w for w in warnings if "trading session" in w)
    unknown_line = next(w for w in warnings if "not in `instruments`" in w)
    assert "issue #4" in calendar_line
    assert "unrelated to issue #4" in unknown_line
    assert "NOCAL" in calendar_line and "AAPLL" in unknown_line


# ---------------------------------------------------------------------------
# Operator-tunable retry cadence (912 review F002 disposition)
# ---------------------------------------------------------------------------


def test_settings_default_matches_the_constant():
    """The env override defaults to the shipped cadence, not a second literal."""
    settings = Settings()
    assert settings.daily_cycle_retry_interval == DAILY_CYCLE_RETRY_INTERVAL


@pytest.mark.parametrize("minutes", [0, -5, 24 * 60 + 1])
def test_settings_rejects_out_of_range_retry_minutes(minutes, monkeypatch):
    """Zero busy-loops; beyond a day the gate stops reopening within the pass."""
    from pydantic import ValidationError

    monkeypatch.setenv("MT_DAILY_CYCLE_RETRY_MINUTES", str(minutes))
    with pytest.raises(ValidationError):
        Settings()


def test_configured_interval_governs_the_gate():
    """A longer interval holds the gate shut where the default would open it."""
    ended = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    state = RunnerState(last_daily_cycle_end_utc=ended)
    at = datetime(2026, 8, 3, 12, 20, tzinfo=UTC)

    assert daily_cycle_due(state, at) is True, "default 15m should have elapsed"
    assert (
        daily_cycle_due(state, at, retry_interval=timedelta(hours=2)) is False
    ), "the configured interval must govern, not the module constant"


def test_configured_interval_governs_the_sleep_horizon():
    """The sleep must track the same interval or it oversleeps a due cycle."""
    ended = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    state = RunnerState(last_daily_cycle_end_utc=ended)
    at = datetime(2026, 8, 3, 12, 5, tzinfo=UTC)
    slept: list[float] = []

    # Uncapped, so the arithmetic is visible rather than clipped to 60s.
    sleep_until_next_due_event(
        state, at, slept.append, cap_seconds=1e9, retry_interval=timedelta(hours=2)
    )
    expected = (timedelta(hours=2) - timedelta(minutes=5)).total_seconds()
    assert slept == [pytest.approx(expected)]


def test_runner_config_carries_the_interval():
    assert RunnerConfig().daily_retry_interval == DAILY_CYCLE_RETRY_INTERVAL
    assert (
        RunnerConfig(daily_retry_interval=timedelta(hours=3)).daily_retry_interval
        == timedelta(hours=3)
    )
