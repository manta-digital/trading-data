"""Seed a migrated tick database with what the purchase planner reads (224).

Owned tier days (verified trades units, as adoption leaves them), the dataset
edge, and per-day conditions. Synchronous psycopg: fixtures, not the code
under test.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import psycopg
from tick_support.rows import (
    FILE_COLUMNS,
    insert_dataset_edge,
    insert_day_condition,
    insert_request,
    insert_unit,
)

from manta_trading.data.tick.constants import DatasetCondition, TickSchema, UnitState

EDGE_END = datetime(2030, 1, 1, tzinfo=UTC)
ADOPTED_AT = datetime(2024, 10, 2, tzinfo=UTC)


def seed_owned_days(
    url: str, days: Iterable[date], schema: TickSchema = TickSchema.TRADES
) -> list[int]:
    """One adopted request holding a *verified* unit per day; returns unit ids."""
    days = sorted(days)
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(
            conn,
            schema=schema.value,
            range_start=days[0],
            range_end=days[-1] + timedelta(days=1),
            is_adopted=True,
            provider_job_id="GLBX-ADOPTED-SEED",
            actual_cost_usd=0,
            requested_at=ADOPTED_AT,
            committed_at=ADOPTED_AT,
        )
        return [
            insert_unit(
                conn,
                request,
                unit_date=day,
                state=UnitState.VERIFIED.value,
                **FILE_COLUMNS,
            )
            for day in days
        ]


def seed_availability(
    url: str,
    days: Iterable[date],
    condition: DatasetCondition = DatasetCondition.AVAILABLE,
) -> None:
    """The dataset edge (far future) and a condition row for each day."""
    with psycopg.connect(url, autocommit=True) as conn:
        insert_dataset_edge(conn, available_end=EDGE_END)
        for day in days:
            insert_day_condition(conn, condition_date=day, condition=condition.value)
