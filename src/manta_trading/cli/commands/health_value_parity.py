"""The ``value parity`` row of ``mt data health`` (slice 173).

``caggs verify`` compares counts, so it cannot see a rollup whose definition
differs from the eligible 1m path. This check can: over the ET day of the
judged session (921's definition, via ``select_judged_session``), every 5m
rollup row for one sampled symbol must equal the 1m bars rolled up again in
SQL with the expressions the rollups are rendered from. Every column is
compared, ``minute_count`` and the NULL prices of an all-marked bucket
included (``None == None``, as ``IS NOT DISTINCT FROM``).

The symbol is the one with the most marked minutes that day, ties by symbol,
so the check always runs the marked-minute paths. A day with no marked minute
fails: there is nothing to prove, and ``off-tape share`` fails on it anyway.

The verdict is pure; only ``fetch_value_parity`` does I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from manta_trading.cli.commands.health import HealthCheck
from manta_trading.cli.commands.minute_session_mass import TradingSessionBounds
from manta_trading.constants import (
    GRANULARITY_SOURCE,
    HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT,
    MINUTE_OHLCV_TABLE,
    OFF_TAPE_DAY_TZ,
    Granularity,
)
from manta_trading.data.quality.off_tape_store import et_day_start
from manta_trading.market.schema.eligible_rollups_sql import (
    ROLLUP_COLUMNS,
    eligible_rollup_select,
)

VALUE_PARITY_CHECK = "value parity"
_GRANULARITY = Granularity.M5
_VIEW = GRANULARITY_SOURCE[_GRANULARITY]
_COMPARED = ROLLUP_COLUMNS[2:]  # every column after the time_bucket, symbol key
_NO_SESSION = "no completed session to judge"


@dataclass(frozen=True)
class BucketPair:
    """One 5m bucket as the rollup stores it and as the recompute gives it;
    ``None`` where that side has no row."""

    bucket: datetime
    stored: tuple[Any, ...] | None
    recomputed: tuple[Any, ...] | None


@dataclass(frozen=True)
class ValueParitySample:
    """The sampled symbol's buckets over one ET day; ``symbol`` is ``None``
    when no minute of the day is marked."""

    day: str
    symbol: str | None
    marked: int
    pairs: tuple[BucketPair, ...]


def _difference(pair: BucketPair) -> str | None:
    at = pair.bucket.astimezone(UTC).isoformat()
    if pair.stored is None:
        return f"bucket {at} is missing from {_VIEW}"
    if pair.recomputed is None:
        return f"bucket {at} is in {_VIEW} but not in the 1m bars"
    differing = [
        f"{column} (rollup {stored}, recomputed {recomputed})"
        for column, stored, recomputed in zip(
            _COMPARED, pair.stored, pair.recomputed, strict=True
        )
        if stored != recomputed
    ]
    if differing:
        return f"bucket {at} differs in " + ", ".join(differing)
    return None


def check_value_parity(sample: ValueParitySample | None) -> HealthCheck:
    """Fail on the first bucket that differs, naming it and its columns."""
    if sample is None:
        return HealthCheck(VALUE_PARITY_CHECK, False, _NO_SESSION)
    if sample.symbol is None:
        return HealthCheck(
            VALUE_PARITY_CHECK,
            False,
            f"{sample.day}: no marked minute, so nothing to compare",
        )
    prefix = f"{sample.day} {sample.symbol}"
    for pair in sample.pairs:
        difference = _difference(pair)
        if difference is not None:
            return HealthCheck(VALUE_PARITY_CHECK, False, f"{prefix}: {difference}")
    return HealthCheck(
        VALUE_PARITY_CHECK,
        True,
        f"{prefix}: {len(sample.pairs)} {_GRANULARITY.value} buckets equal the "
        f"eligible 1m bars ({sample.marked:,} marked minutes)",
    )


_SAMPLE_SQL = f"""
    SELECT symbol, count(*) AS marked
    FROM {MINUTE_OHLCV_TABLE}
    WHERE time >= %(start)s AND time < %(end)s AND off_tape <> 0
    GROUP BY symbol
    ORDER BY marked DESC, symbol
    LIMIT 1
"""

_STORED = ", ".join(_COMPARED)


def _pairs_sql() -> str:
    """Both sides of every bucket of the symbol's day, full-joined on bucket."""
    recomputed = ", ".join(f"r.{c}" for c in _COMPARED)
    stored = ", ".join(f"s.{c}" for c in _COMPARED)
    return f"""
        WITH recomputed AS (
            SELECT{eligible_rollup_select(_GRANULARITY)}
            FROM {MINUTE_OHLCV_TABLE}
            WHERE symbol = %(symbol)s AND time >= %(start)s AND time < %(end)s
            GROUP BY time_bucket, symbol
        ), stored AS (
            SELECT time_bucket, {_STORED}
            FROM {_VIEW}
            WHERE symbol = %(symbol)s
              AND time_bucket >= %(start)s AND time_bucket < %(end)s
        )
        SELECT COALESCE(r.time_bucket, s.time_bucket) AS bucket,
               s.time_bucket IS NOT NULL AS has_stored,
               r.time_bucket IS NOT NULL AS has_recomputed,
               {stored}, {recomputed}
        FROM recomputed r FULL JOIN stored s ON s.time_bucket = r.time_bucket
        ORDER BY bucket
    """


def _pair(row: tuple[Any, ...]) -> BucketPair:
    width = len(_COMPARED)
    stored, recomputed = row[3 : 3 + width], row[3 + width :]
    return BucketPair(
        row[0], tuple(stored) if row[1] else None, tuple(recomputed) if row[2] else None
    )


def fetch_value_parity(
    conn: psycopg.Connection[Any], session: TradingSessionBounds
) -> ValueParitySample:
    """Sample the most-marked symbol of the session's ET day and pair up its
    stored and recomputed 5m buckets."""
    day = session.session_open_utc.astimezone(OFF_TAPE_DAY_TZ).date()
    params: dict[str, Any] = {
        "start": et_day_start(day),
        "end": et_day_start(day + timedelta(days=1)),
    }
    with conn.cursor() as cur:
        cur.execute(
            f"SET LOCAL statement_timeout = '{HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT}'"
        )
        cur.execute(_SAMPLE_SQL, params)
        sampled = cur.fetchone()
        if sampled is None:
            return ValueParitySample(day.isoformat(), None, 0, ())
        params["symbol"] = sampled[0]
        cur.execute(_pairs_sql(), params)
        pairs = tuple(_pair(row) for row in cur.fetchall())
    return ValueParitySample(day.isoformat(), sampled[0], int(sampled[1]), pairs)


def gather_value_parity(
    conn: psycopg.Connection[Any], session: TradingSessionBounds | None
) -> list[HealthCheck]:
    """The row for the judged session; ``None`` fails it explicitly."""
    sample = fetch_value_parity(conn, session) if session is not None else None
    return [check_value_parity(sample)]
