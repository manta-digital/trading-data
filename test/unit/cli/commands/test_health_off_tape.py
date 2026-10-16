"""The off-tape rows of ``mt data health`` (slice 171)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from manta_trading.cli.commands.health import render_lines
from manta_trading.cli.commands.health_off_tape import (
    SHARE_CHECK,
    UNEVALUATED_CHECK,
    OffTapeCounts,
    check_share,
    check_unevaluated,
    fetch_off_tape_counts,
    gather_off_tape,
)
from manta_trading.cli.commands.minute_session_mass import TradingSessionBounds
from manta_trading.constants import OFF_TAPE_SHARE_CEILING, OFF_TAPE_SHARE_FLOOR

BARS = 2_000_000
HEALTHY = OffTapeCounts("2026-10-09", BARS, 0, 6_400)


def _marked(share: float) -> OffTapeCounts:
    return OffTapeCounts("2026-10-09", BARS, 0, round(BARS * share))


def test_unevaluated_passes_when_every_bar_is_marked() -> None:
    check = check_unevaluated(HEALTHY)
    assert check.ok
    assert check.detail == "2026-10-09: all 2,000,000 bars evaluated"


def test_unevaluated_fails_with_the_count() -> None:
    check = check_unevaluated(OffTapeCounts("2026-10-09", BARS, 12, 6_400))
    assert not check.ok
    assert "12 of 2,000,000 bars have no off-tape mark" in check.detail


def test_share_passes_inside_bounds() -> None:
    check = check_share(HEALTHY)
    assert check.ok
    assert "6,400 of 2,000,000 bars marked (0.320%" in check.detail


@pytest.mark.parametrize(
    "share", [OFF_TAPE_SHARE_FLOOR / 2, 0.0, OFF_TAPE_SHARE_CEILING * 2]
)
def test_share_fails_outside_bounds(share: float) -> None:
    check = check_share(_marked(share))
    assert not check.ok
    assert f"bounds {OFF_TAPE_SHARE_FLOOR:.3%}–{OFF_TAPE_SHARE_CEILING:.3%}" in (
        check.detail
    )


def test_share_bounds_are_inclusive() -> None:
    assert check_share(_marked(OFF_TAPE_SHARE_FLOOR)).ok
    assert check_share(_marked(OFF_TAPE_SHARE_CEILING)).ok


def test_no_bars_fails() -> None:
    assert not check_share(OffTapeCounts("2026-10-09", 0, 0, 0)).ok


def test_no_session_fails_both() -> None:
    checks = gather_off_tape(MagicMock(), None)
    assert [(c.name, c.ok) for c in checks] == [
        (UNEVALUATED_CHECK, False),
        (SHARE_CHECK, False),
    ]


def test_rendered_lines_name_both_checks() -> None:
    lines = render_lines([check_unevaluated(HEALTHY), check_share(HEALTHY)])
    assert any(UNEVALUATED_CHECK in line for line in lines)
    assert any(SHARE_CHECK in line for line in lines)


def test_fetch_reads_the_sessions_et_day() -> None:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (BARS, 0, 6_400)
    session = TradingSessionBounds(
        datetime(2026, 1, 7, 14, 30, tzinfo=UTC), datetime(2026, 1, 7, 21, tzinfo=UTC)
    )
    counts = fetch_off_tape_counts(conn, session)
    assert counts == OffTapeCounts("2026-01-07", BARS, 0, 6_400)
    _sql, (start, end) = cursor.execute.call_args.args
    # 00:00 EST = 05:00 UTC, through the next ET midnight.
    assert start.astimezone(UTC) == datetime(2026, 1, 7, 5, tzinfo=UTC)
    assert end.astimezone(UTC) == datetime(2026, 1, 8, 5, tzinfo=UTC)
