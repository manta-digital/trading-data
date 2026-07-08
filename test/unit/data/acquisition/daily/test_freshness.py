"""Unit tests for freshness.py — pure functions, no I/O."""

from __future__ import annotations

import datetime

import pytest

from manta_trading.data.acquisition.daily.freshness import (
    MIN_DAYS,
    RECENT_DAYS,
    _is_attempt_fresh,
    _is_fresh,
    _resolve_output_size,
)

_TODAY = datetime.date(2026, 4, 11)


def _ts(days_ago: int) -> datetime.datetime:
    """Return a UTC datetime *days_ago* days before _TODAY."""
    d = _TODAY - datetime.timedelta(days=days_ago)
    return datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# _resolve_output_size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "last_success_ts, expected",
    [
        (None, "full"),
        (_ts(0), "compact"),
        (_ts(1), "compact"),
        (_ts(100), "compact"),
        (_ts(101), "full"),
        (_ts(365), "full"),
    ],
)
def test_resolve_output_size(last_success_ts, expected):
    result = _resolve_output_size(last_success_ts, today=_TODAY)
    assert result == expected


def test_resolve_output_size_boundary_exactly_recent_days():
    """Gap of exactly RECENT_DAYS → compact (≤ threshold)."""
    ts = _ts(RECENT_DAYS)
    assert _resolve_output_size(ts, today=_TODAY) == "compact"


def test_resolve_output_size_one_over_threshold():
    """Gap of RECENT_DAYS + 1 → full."""
    ts = _ts(RECENT_DAYS + 1)
    assert _resolve_output_size(ts, today=_TODAY) == "full"


# ---------------------------------------------------------------------------
# _is_fresh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "last_success_ts, expected",
    [
        (None, False),
        (_ts(0), True),   # today → gap 0 < 5 → fresh
        (_ts(1), True),   # yesterday → gap 1 < 5 → fresh
        (_ts(4), True),   # 4 days ago → gap 4 < 5 → fresh (weekend tolerance)
        (_ts(5), False),  # 5 days ago → gap 5, not < 5 → stale
        (_ts(100), False),
    ],
)
def test_is_fresh(last_success_ts, expected):
    result = _is_fresh(last_success_ts, today=_TODAY)
    assert result == expected


def test_is_fresh_min_days_boundary():
    """Gap of exactly MIN_DAYS → stale (boundary: not strictly less than)."""
    ts = _ts(MIN_DAYS)
    assert _is_fresh(ts, today=_TODAY) is False


def test_is_fresh_custom_min_days():
    """Custom min_days override works independently of the constant."""
    ts = _ts(5)
    assert _is_fresh(ts, min_days=6, today=_TODAY) is True
    assert _is_fresh(ts, min_days=5, today=_TODAY) is False


# ---------------------------------------------------------------------------
# Purity / no magic numbers
# ---------------------------------------------------------------------------


def test_no_io_in_resolve(monkeypatch):
    """_resolve_output_size is pure — passes today explicitly, no side effects."""
    # If it accidentally called date.today() without the override it would use
    # the real today; we pass a fixed date and verify the result is deterministic.
    ts = _ts(50)
    assert _resolve_output_size(ts, today=_TODAY) == "compact"
    assert _resolve_output_size(ts, today=_TODAY) == "compact"


def test_no_io_in_is_fresh(monkeypatch):
    """_is_fresh is pure — deterministic given the same inputs."""
    ts = _ts(1)
    assert _is_fresh(ts, today=_TODAY) is True
    assert _is_fresh(ts, today=_TODAY) is True


def test_constants_are_the_only_magic_numbers():
    """MIN_DAYS and RECENT_DAYS are defined and hold the expected sentinel values."""
    assert MIN_DAYS == 5
    assert RECENT_DAYS == 100


# ---------------------------------------------------------------------------
# Timezone regression — last_success_ts may come back from Postgres in a
# non-UTC session tz. The gap must be computed on UTC calendar dates on both
# sides, otherwise a symbol written at e.g. 2026-04-10 00:00 UTC reads back
# as 2026-04-09 18:00-06 and looks one day older than it really is.
# ---------------------------------------------------------------------------


def test_is_fresh_non_utc_tz_aware_ts():
    """A tz-aware ts in a western timezone must not be read as one day older."""
    # 2026-04-10 00:00 UTC == 2026-04-09 18:00 America/Denver (-06)
    mdt = datetime.timezone(datetime.timedelta(hours=-6))
    ts = datetime.datetime(2026, 4, 9, 18, 0, 0, tzinfo=mdt)
    # Today is 2026-04-11 UTC → gap (UTC) == 1 → fresh.
    assert _is_fresh(ts, today=_TODAY) is True


def test_resolve_output_size_non_utc_tz_aware_ts():
    """A tz-aware ts in a western timezone must not shift into a different bucket."""
    mdt = datetime.timezone(datetime.timedelta(hours=-6))
    # 2026-04-10 00:00 UTC expressed in -06 local is 2026-04-09 18:00-06.
    # The local-tz date (April 9) is one day earlier than the UTC date (April 10).
    # With _TODAY = 2026-04-11 UTC, the UTC gap is 1 day → compact.
    ts = datetime.datetime(2026, 4, 10, 0, 0, 0, tzinfo=datetime.timezone.utc).astimezone(mdt)
    assert _resolve_output_size(ts, today=_TODAY) == "compact"
    # Proves we didn't regress on the boundary either.
    ts_boundary = datetime.datetime(
        _TODAY.year, _TODAY.month, _TODAY.day, 0, 0, 0, tzinfo=datetime.timezone.utc
    ) - datetime.timedelta(days=100)
    ts_boundary = ts_boundary.astimezone(mdt)
    assert _resolve_output_size(ts_boundary, today=_TODAY) == "compact"


# ---------------------------------------------------------------------------
# _is_attempt_fresh — keys on last_attempt_ts so dormant/delisted symbols
# whose last data is years old are correctly skipped on the next cycle once
# we've confirmed today there's nothing new to fetch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "last_attempt_ts, expected",
    [
        (None, False),
        (_ts(0), True),
        (_ts(1), True),
        (_ts(MIN_DAYS - 1), True),
        (_ts(MIN_DAYS), False),
        (_ts(MIN_DAYS + 1), False),
        (_ts(365), False),
    ],
)
def test_is_attempt_fresh(last_attempt_ts, expected):
    assert _is_attempt_fresh(last_attempt_ts, today=_TODAY) is expected


def test_is_attempt_fresh_decouples_from_last_success_ts():
    """Regression: a delisted symbol whose last bar is years old but which
    we attempted today should be considered attempt-fresh — Fix A from the
    slice 128 dry-run, which avoided the daemon hammering ~1k dormant
    tickers every cycle and burning ~63k API calls per 24h."""
    long_ago_data = _ts(3000)  # last bar from years ago
    attempted_today = _ts(0)
    # Old-style _is_fresh would say "not fresh, re-fetch":
    assert _is_fresh(long_ago_data, today=_TODAY) is False
    # New _is_attempt_fresh says "attempted today, skip":
    assert _is_attempt_fresh(attempted_today, today=_TODAY) is True


def test_is_attempt_fresh_custom_min_days():
    ts = _ts(5)
    assert _is_attempt_fresh(ts, min_days=6, today=_TODAY) is True
    assert _is_attempt_fresh(ts, min_days=5, today=_TODAY) is False
