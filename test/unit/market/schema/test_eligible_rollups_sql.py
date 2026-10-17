"""Rendering of migration 063's SQL (slice 172)."""

from __future__ import annotations

import re

import pytest

from manta_trading.constants import (
    CAGG_NEXT_SUFFIX,
    GRANULARITY_SOURCE,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_COVERAGE_VIEW,
    MINUTE_OHLCV_TABLE,
    Granularity,
)
from manta_trading.market.schema.eligible_rollups_sql import (
    NEXT_COVERAGE_PARENT,
    NEXT_COVERAGE_VIEW,
    NEXT_ROLLUP_VIEWS,
    eligible_rollup_sql,
    eligible_rollups_next_statements,
    minute_coverage_sql,
)

# Migration 051's minute_coverage statement as it read before the extraction.
_PRE_EXTRACTION_051 = """
                CREATE MATERIALIZED VIEW IF NOT EXISTS minute_coverage
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket(
                        INTERVAL '604800 seconds',
                        time_bucket
                    ) AS time_bucket,
                    symbol,
                    SUM(minute_count) AS bars,
                    MIN(time_bucket)  AS first_bucket,
                    MAX(time_bucket)  AS last_bucket
                FROM minute_4hour_ohlcv
                GROUP BY 1, symbol
                WITH NO DATA
                """


def test_coverage_sql_for_current_names_is_051_unchanged() -> None:
    rendered = minute_coverage_sql(
        MINUTE_COVERAGE_VIEW, GRANULARITY_SOURCE[Granularity.H4]
    )
    assert rendered == _PRE_EXTRACTION_051


def test_next_names_are_derived() -> None:
    assert NEXT_ROLLUP_VIEWS == tuple(
        GRANULARITY_SOURCE[g] + CAGG_NEXT_SUFFIX for g in MINUTE_CAGG_GRANULARITIES
    )
    assert NEXT_COVERAGE_VIEW == MINUTE_COVERAGE_VIEW + CAGG_NEXT_SUFFIX
    assert NEXT_COVERAGE_PARENT == NEXT_ROLLUP_VIEWS[-1]


@pytest.mark.parametrize("granularity", MINUTE_CAGG_GRANULARITIES)
def test_rollup_reads_the_raw_table_directly(granularity: Granularity) -> None:
    sql = eligible_rollup_sql(granularity)
    from_clause = sql.split(" FROM ")[-1]
    assert from_clause.split()[0] == MINUTE_OHLCV_TABLE
    assert "SELECT" not in from_clause, "a cagg rejects a sub-query in FROM"
    assert "WITH NO DATA" in sql


@pytest.mark.parametrize("granularity", MINUTE_CAGG_GRANULARITIES)
def test_rollup_columns_match_the_current_rollups(granularity: Granularity) -> None:
    aliases = re.findall(r"AS (\w+),?\n", eligible_rollup_sql(granularity))
    assert aliases == [
        "time_bucket",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "minute_count",
    ]


def test_every_price_and_volume_is_filtered_but_count_is_not() -> None:
    sql = eligible_rollup_sql(Granularity.M5)
    assert sql.count("FILTER (WHERE") == 5
    assert "COUNT(*) AS minute_count" in sql


def test_statement_order_rollups_then_coverage_then_policies() -> None:
    statements = eligible_rollups_next_statements()
    creates = [s for s in statements if "CREATE MATERIALIZED VIEW" in s]
    assert [re.search(r"EXISTS (\w+)", s)[1] for s in creates] == [
        *NEXT_ROLLUP_VIEWS,
        NEXT_COVERAGE_VIEW,
    ]
    last_create = max(i for i, s in enumerate(statements) if s in creates)
    first_policy = min(
        i for i, s in enumerate(statements) if "add_continuous_aggregate_policy" in s
    )
    assert last_create < first_policy
