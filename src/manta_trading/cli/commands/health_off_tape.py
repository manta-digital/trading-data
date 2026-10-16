"""The off-tape rows of ``mt data health`` (slice 171).

Both judge the ET day of the last collected minute session (921's
definition, via ``select_judged_session``; this module never redefines it):

- ``off-tape unevaluated``: any ``NULL`` mark fails. Insert paths never commit
  unevaluated rows, so a ``NULL`` means a path skipped marking.
- ``off-tape share``: the share of bars with a non-zero mask must lie in
  ``[OFF_TAPE_SHARE_FLOOR, OFF_TAPE_SHARE_CEILING]``. Zero means the detector
  is not running; a flood means the provider or the detector changed.

Split from ``health.py`` to keep it near ~300 lines (the ``health_tick.py``
pattern). The verdicts are pure; only ``fetch_off_tape_counts`` does I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psycopg

from manta_trading.cli.commands.health import HealthCheck
from manta_trading.cli.commands.minute_session_mass import TradingSessionBounds
from manta_trading.constants import (
    HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT,
    OFF_TAPE_DAY_TZ,
    OFF_TAPE_SHARE_CEILING,
    OFF_TAPE_SHARE_FLOOR,
)
from manta_trading.data.quality.off_tape_store import et_day_start

UNEVALUATED_CHECK = "off-tape unevaluated"
SHARE_CHECK = "off-tape share"
_NO_SESSION = "no completed session to judge"


@dataclass(frozen=True)
class OffTapeCounts:
    """Mark counts over one ET day of ``minute_ohlcv``."""

    day: str
    bars: int
    unevaluated: int
    marked: int


def fetch_off_tape_counts(
    conn: psycopg.Connection[Any], session: TradingSessionBounds
) -> OffTapeCounts:
    """Count bars, ``NULL`` marks and non-zero marks over the session's ET day."""
    day = session.session_open_utc.astimezone(OFF_TAPE_DAY_TZ).date()
    start, end = et_day_start(day), et_day_start(day + timedelta(days=1))
    with conn.cursor() as cur:
        cur.execute(
            f"SET LOCAL statement_timeout = '{HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT}'"
        )
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE off_tape IS NULL),"
            " count(*) FILTER (WHERE off_tape <> 0)"
            " FROM minute_ohlcv WHERE time >= %s AND time < %s",
            (start, end),
        )
        row = cur.fetchone()
    assert row is not None  # an aggregate without GROUP BY always returns a row
    return OffTapeCounts(day.isoformat(), int(row[0]), int(row[1]), int(row[2]))


def check_unevaluated(counts: OffTapeCounts | None) -> HealthCheck:
    if counts is None:
        return HealthCheck(UNEVALUATED_CHECK, False, _NO_SESSION)
    if counts.unevaluated:
        return HealthCheck(
            UNEVALUATED_CHECK,
            False,
            f"{counts.day}: {counts.unevaluated:,} of {counts.bars:,} bars have "
            "no off-tape mark — an insert path skipped marking",
        )
    return HealthCheck(
        UNEVALUATED_CHECK, True, f"{counts.day}: all {counts.bars:,} bars evaluated"
    )


def check_share(
    counts: OffTapeCounts | None,
    *,
    floor: float = OFF_TAPE_SHARE_FLOOR,
    ceiling: float = OFF_TAPE_SHARE_CEILING,
) -> HealthCheck:
    if counts is None:
        return HealthCheck(SHARE_CHECK, False, _NO_SESSION)
    if counts.bars == 0:
        return HealthCheck(SHARE_CHECK, False, f"{counts.day}: no bars")
    share = counts.marked / counts.bars
    ok = floor <= share <= ceiling
    return HealthCheck(
        SHARE_CHECK,
        ok,
        f"{counts.day}: {counts.marked:,} of {counts.bars:,} bars marked "
        f"({share:.3%}; bounds {floor:.3%}–{ceiling:.3%})",
    )


def gather_off_tape(
    conn: psycopg.Connection[Any], session: TradingSessionBounds | None
) -> list[HealthCheck]:
    """Both rows for the judged session; ``None`` fails both explicitly."""
    counts = fetch_off_tape_counts(conn, session) if session is not None else None
    return [check_unevaluated(counts), check_share(counts)]
