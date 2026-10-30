"""``is_plan_included`` and the plan table (slice 300, D6, D7)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from manta_trading.config.databento_plans import (
    PLAN_DATASET,
    PLAN_INCLUDED_HISTORY,
    DatabentoPlan,
)
from manta_trading.data.tick.constants import CME_DATASET, TickSchema
from manta_trading.data.tick.plan_inclusion import is_plan_included

AT = datetime(2026, 10, 30, 12, tzinfo=UTC)
TODAY = AT.date()
STANDARD = DatabentoPlan.STANDARD


def _included(
    schema: TickSchema,
    start: date,
    end: date | None = None,
    *,
    plan: DatabentoPlan | None = STANDARD,
    dataset: str = CME_DATASET,
) -> bool:
    end = start + timedelta(days=1) if end is None else end
    return is_plan_included(plan, dataset, schema, start, end, AT)


@pytest.mark.parametrize("plan", list(DatabentoPlan))
def test_every_plan_covers_every_schema(plan: DatabentoPlan) -> None:
    assert set(PLAN_INCLUDED_HISTORY[plan]) == set(TickSchema)


def test_plans_cover_the_cme_dataset() -> None:
    assert PLAN_DATASET == CME_DATASET


@pytest.mark.parametrize(
    "schema", [TickSchema.TRADES, TickSchema.TBBO, TickSchema.MBP_1]
)
def test_a_recent_tick_request_is_included(schema: TickSchema) -> None:
    assert _included(schema, TODAY - timedelta(days=30))


def test_an_old_tick_request_is_not_included() -> None:
    assert not _included(TickSchema.TBBO, TODAY - timedelta(days=700))


def test_the_365th_day_back_is_included_and_the_366th_is_not() -> None:
    assert _included(TickSchema.TRADES, TODAY - timedelta(days=365))
    assert not _included(TickSchema.TRADES, TODAY - timedelta(days=366))


def test_a_request_straddling_the_boundary_is_not_included() -> None:
    start = TODAY - timedelta(days=367)
    assert not _included(TickSchema.TRADES, start, start + timedelta(days=5))


@pytest.mark.parametrize("days_back", [1, 400, 3000])
def test_definition_is_included_at_any_age(days_back: int) -> None:
    assert _included(TickSchema.DEFINITION, TODAY - timedelta(days=days_back))


def test_another_dataset_is_never_included() -> None:
    assert not _included(
        TickSchema.TRADES, TODAY - timedelta(days=1), dataset="XNAS.ITCH"
    )


def test_no_plan_includes_nothing() -> None:
    assert not _included(TickSchema.DEFINITION, TODAY - timedelta(days=1), plan=None)


@pytest.mark.parametrize("delta_days", [0, -1])
def test_an_empty_or_inverted_range_raises(delta_days: int) -> None:
    start = TODAY - timedelta(days=10)
    with pytest.raises(ValueError, match="empty request range"):
        _included(TickSchema.TRADES, start, start + timedelta(days=delta_days))


def test_a_naive_judging_time_raises() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_plan_included(
            STANDARD,
            CME_DATASET,
            TickSchema.TRADES,
            date(2026, 10, 1),
            date(2026, 10, 2),
            datetime(2026, 10, 30),
        )
