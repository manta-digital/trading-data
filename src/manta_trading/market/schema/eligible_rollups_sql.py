"""SQL for the eligible ``_next`` minute rollups and their coverage (slice 172).

Migration 063 builds ``minute_{5min,15min,hourly,4hour}_ohlcv_next`` beside the
current rollups, plus ``minute_coverage_next`` over the 4h one. Each price is
the bar's clamped eligible value (``eligible_sql``) and, like volume, is
aggregated only over bars with at least one unmarked price. ``minute_count``
counts every bar, so coverage, accounting and repair's count parity keep their
meaning. A bucket of only all-marked bars keeps its row, with NULL prices and
volume. A cagg rejects a sub-query in ``FROM``, so the expressions are inline.

Kept out of ``migrations/minute.py`` so that file does not grow; it imports
this one, never the reverse.
"""

from __future__ import annotations

from datetime import timedelta

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    GRANULARITY_SOURCE,
    MINUTE_CAGG_BUCKET_WIDTH,
    MINUTE_CAGG_CHUNK_INTERVAL,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_CAGG_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_REFRESH_END_OFFSET,
    MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_VIEW,
    MINUTE_OHLCV_TABLE,
    CaggTarget,
    Granularity,
    target_view_name,
)
from manta_trading.market.schema.eligible_sql import (
    eligible_value,
    has_eligible_price,
)

_REFRESH_PROC = "policy_refresh_continuous_aggregate"


def interval_seconds_sql(td: timedelta) -> str:
    """Render a timedelta as a seconds-based SQL INTERVAL literal (exact)."""
    return f"INTERVAL '{int(td.total_seconds())} seconds'"


NEXT_ROLLUP_VIEWS: tuple[str, ...] = tuple(
    target_view_name(GRANULARITY_SOURCE[g], CaggTarget.NEXT)
    for g in MINUTE_CAGG_GRANULARITIES
)
"""The four eligible rollups, smallest bucket first."""

NEXT_COVERAGE_VIEW: str = target_view_name(MINUTE_COVERAGE_VIEW, CaggTarget.NEXT)
NEXT_COVERAGE_PARENT: str = target_view_name(
    GRANULARITY_SOURCE[Granularity.H4], CaggTarget.NEXT
)


def minute_coverage_sql(view: str, parent: str) -> str:
    """``CREATE MATERIALIZED VIEW`` for a minute coverage cagg over ``parent``.

    Migration 051's definition, extracted unchanged: called with the current
    names it renders 051's text byte for byte; 063 calls it with the ``_next``
    names.
    """
    return f"""
                CREATE MATERIALIZED VIEW IF NOT EXISTS {view}
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket(
                        {interval_seconds_sql(COVERAGE_BUCKET_INTERVAL)},
                        time_bucket
                    ) AS time_bucket,
                    symbol,
                    SUM(minute_count) AS bars,
                    MIN(time_bucket)  AS first_bucket,
                    MAX(time_bucket)  AS last_bucket
                FROM {parent}
                GROUP BY 1, symbol
                WITH NO DATA
                """


def eligible_rollup_sql(granularity: Granularity) -> str:
    """``CREATE MATERIALIZED VIEW`` for one eligible ``_next`` minute rollup.

    Column names and order match migration 033's current rollups.
    """
    view = target_view_name(GRANULARITY_SOURCE[granularity], CaggTarget.NEXT)
    width = interval_seconds_sql(MINUTE_CAGG_BUCKET_WIDTH[granularity])
    eligible = f"FILTER (WHERE {has_eligible_price()})"
    return f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {view}
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket({width}, time) AS time_bucket,
            symbol,
            FIRST({eligible_value("open")}, time) {eligible} AS open,
            MAX({eligible_value("high")}) {eligible} AS high,
            MIN({eligible_value("low")}) {eligible} AS low,
            LAST({eligible_value("close")}, time) {eligible} AS close,
            SUM(volume) {eligible} AS volume,
            COUNT(*) AS minute_count
        FROM {MINUTE_OHLCV_TABLE}
        GROUP BY time_bucket, symbol
        WITH NO DATA
        """


def _chunk_interval_sql(view: str) -> str:
    return (
        f"SELECT set_chunk_time_interval('{view}'::regclass, "
        f"{interval_seconds_sql(MINUTE_CAGG_CHUNK_INTERVAL)})"
    )


def _refresh_policy_sql(
    view: str, start: timedelta, end: timedelta, schedule: timedelta
) -> str:
    """Add ``view``'s refresh policy unless it already has one."""
    return f"""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM timescaledb_information.jobs
                WHERE hypertable_name = '{view}'
                  AND proc_name = '{_REFRESH_PROC}'
            ) THEN
                PERFORM add_continuous_aggregate_policy('{view}',
                    start_offset      => {interval_seconds_sql(start)},
                    end_offset        => {interval_seconds_sql(end)},
                    schedule_interval => {interval_seconds_sql(schedule)});
            END IF;
        END $$;
        """


def _rollup_refresh_policy_sql(granularity: Granularity) -> str:
    """The current twin's policy: the shared start_offset, ``end_offset`` one
    bucket, scheduled every bucket but at most hourly (migrations 035, 061)."""
    width = MINUTE_CAGG_BUCKET_WIDTH[granularity]
    return _refresh_policy_sql(
        target_view_name(GRANULARITY_SOURCE[granularity], CaggTarget.NEXT),
        MINUTE_CAGG_REFRESH_START_OFFSET,
        width,
        min(width, MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL),
    )


def eligible_rollups_next_statements() -> list[str]:
    """Migration 063's statements in order, one cagg DDL per statement.

    The four rollups (each with its chunk interval), then
    ``minute_coverage_next`` over the 4h one, then the refresh policies.
    Every statement is guarded, so a rerun after a partial apply converges.
    The columnstore policies are added by the migration's Python step.
    """
    statements: list[str] = []
    for granularity, view in zip(
        MINUTE_CAGG_GRANULARITIES, NEXT_ROLLUP_VIEWS, strict=True
    ):
        statements.append(eligible_rollup_sql(granularity))
        statements.append(_chunk_interval_sql(view))
    statements.append(minute_coverage_sql(NEXT_COVERAGE_VIEW, NEXT_COVERAGE_PARENT))
    statements.extend(_rollup_refresh_policy_sql(g) for g in MINUTE_CAGG_GRANULARITIES)
    statements.append(
        _refresh_policy_sql(
            NEXT_COVERAGE_VIEW,
            MINUTE_COVERAGE_REFRESH_START_OFFSET,
            MINUTE_COVERAGE_REFRESH_END_OFFSET,
            MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
        )
    )
    return statements
