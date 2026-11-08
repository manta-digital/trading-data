"""``futures_completeness`` and its progress recorder (slice 305, D3, D5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from manta_trading.data.tick import family_report
from manta_trading.data.tick.family_report import (
    FamilyStatus,
    ReadProgress,
    ReadStep,
    futures_completeness,
    unavailable_with_progress,
)
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import Edge
from manta_trading.data.tick.universe import TICK_UNIVERSE

ES, GC = (replace(e, history=()) for e in TICK_UNIVERSE[:2])
#: The stubbed readers never touch the connection.
NO_CONN: Any = None
EDGE = Edge(datetime(2026, 11, 6, tzinfo=UTC), datetime(2026, 11, 6, tzinfo=UTC))


class _Stubs:
    """Counts the reads the stubbed ``futures_completeness`` makes."""

    def __init__(self) -> None:
        self.edge_reads = 0
        self.products: list[str] = []


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> _Stubs:
    seen = _Stubs()

    async def dataset_edge(conn: Any, dataset: str) -> Edge:
        seen.edge_reads += 1
        return EDGE

    async def family_reports(
        conn: Any, url: str, entry: Any, edge: Any, **kwargs: Any
    ) -> tuple[FamilyStatus, ...]:
        seen.products.append(entry.product)
        kwargs["progress"].at(ReadStep.SCOPE, entry.product, SchemaFamily.TICK_TIER)
        return ()

    monkeypatch.setattr(family_report, "dataset_edge", dataset_edge)
    monkeypatch.setattr(family_report, "family_reports", family_reports)
    return seen


async def test_two_products_are_reported_and_the_dataset_edge_is_read_once(
    stubs: _Stubs,
) -> None:
    result = await futures_completeness(
        NO_CONN,
        "calendar",
        [ES, GC],
        ReadProgress(),
        cross_tier=False,
    )
    assert list(result) == [ES.product, GC.product]
    assert stubs.edge_reads == 1


async def test_a_failure_propagates_and_progress_names_where_it_stopped(
    stubs: _Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(
        conn: Any, url: str, entry: Any, edge: Any, **kwargs: Any
    ) -> tuple[FamilyStatus, ...]:
        kwargs["progress"].at(ReadStep.TRADED_VS_CLEARED, "GC", SchemaFamily.STATISTICS)
        raise TimeoutError("canceling statement due to statement timeout")

    monkeypatch.setattr(family_report, "family_reports", failing)
    progress = ReadProgress()
    with pytest.raises(TimeoutError, match="statement timeout"):
        await futures_completeness(
            NO_CONN,
            "calendar",
            [ES, GC],
            progress,
            cross_tier=True,
        )
    assert (progress.product, progress.family, progress.step) == (
        "GC",
        SchemaFamily.STATISTICS,
        ReadStep.TRADED_VS_CLEARED,
    )
    assert (
        unavailable_with_progress(
            progress, "canceling statement due to statement timeout"
        )
        == "GC statistics: traded_vs_cleared: canceling statement due to statement"
        " timeout"
    )


def test_a_failure_before_any_product_names_only_the_step() -> None:
    progress = ReadProgress()
    progress.at(ReadStep.DATASET_EDGE, None, None)
    assert unavailable_with_progress(progress, "refused") == "dataset edge: refused"
