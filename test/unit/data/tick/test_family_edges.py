"""The per-family source edge (slice 305 D1, D2)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.family_edges import FAMILY_PUBLICATION_LAG, source_edge
from manta_trading.data.tick.schema_families import FAMILY_LEDGER, SchemaFamily
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_PUBLICATION_SESSIONS,
)
from manta_trading.data.tick.status_reads import Edge

CHICAGO = ZoneInfo("America/Chicago")
OBSERVED = datetime(2026, 11, 6, 12, tzinfo=UTC)
STATS, TICKS, BARS = (
    SchemaFamily.STATISTICS,
    SchemaFamily.TICK_TIER,
    SchemaFamily.BAR_1M,
)


def _session(day: date, *, opened: date | None = None) -> Session:
    """CME equity's regular 17:00 -> 16:00 Chicago, dated by its close day."""
    start = opened or day - timedelta(days=1)
    return Session(
        "CME_EQUITY",
        day,
        datetime.combine(start, time(17), CHICAGO).astimezone(UTC),
        datetime.combine(day, time(16), CHICAGO).astimezone(UTC),
    )


def _edge(available_end: datetime) -> Edge:
    return Edge(available_end, OBSERVED)


def _utc(day: date, hour: int = 0) -> datetime:
    return datetime.combine(day, time(hour), UTC)


def test_every_ledger_family_has_a_lag() -> None:
    assert FAMILY_PUBLICATION_LAG.keys() == FAMILY_LEDGER.keys()


def test_the_statistics_lag_is_the_publication_constant() -> None:
    assert FAMILY_PUBLICATION_LAG[STATS] == STATISTICS_PUBLICATION_SESSIONS


@pytest.mark.parametrize("family", [TICKS, BARS])
def test_a_family_with_no_lag_is_bounded_by_the_earlier_of_want_end_and_edge(
    family: SchemaFamily,
) -> None:
    edge = _edge(_utc(date(2024, 12, 20)))
    assert source_edge(family, date(2024, 12, 10), edge, []) == _utc(date(2024, 12, 10))
    assert source_edge(family, date(2024, 12, 31), edge, []) == edge.available_end
    assert source_edge(family, None, edge, []) == edge.available_end


def test_statistics_stop_at_the_last_session_whose_window_has_closed() -> None:
    tuesday, wednesday, thursday = (
        date(2024, 12, 3),
        date(2024, 12, 4),
        date(2024, 12, 5),
    )
    calendar = [_session(tuesday), _session(wednesday), _session(thursday)]
    # Thursday 12:00Z is after Wednesday's close (Wed 22:00Z) and before
    # Thursday's: Tuesday's window (through Wednesday's close) has closed,
    # Wednesday's (through Thursday's close) has not.
    edge = _edge(_utc(thursday, 12))
    assert source_edge(STATS, None, edge, calendar) == calendar[0].close_utc
    # Once Thursday has closed, Wednesday's window has too.
    edge = _edge(_utc(thursday, 23))
    assert source_edge(STATS, None, edge, calendar) == calendar[1].close_utc


def test_statistics_edge_crosses_a_weekend() -> None:
    friday, monday = date(2024, 12, 6), date(2024, 12, 9)
    thursday = date(2024, 12, 5)
    calendar = [
        _session(thursday),
        _session(friday),
        _session(monday, opened=date(2024, 12, 8)),
    ]
    # Monday 12:00Z: Friday's window needs Monday's close, so Thursday is last.
    edge = _edge(_utc(monday, 12))
    assert source_edge(STATS, None, edge, calendar) == calendar[0].close_utc
    # After Monday's close Friday's window is closed (Saturday has no session).
    assert source_edge(STATS, None, _edge(_utc(monday, 23)), calendar) == (
        calendar[1].close_utc
    )


def test_statistics_edge_crosses_a_holiday() -> None:
    wednesday, friday = date(2024, 11, 27), date(2024, 11, 29)
    calendar = [_session(wednesday), _session(friday, opened=date(2024, 11, 28))]
    # Thanksgiving has no session: Wednesday's window ends at Friday's close.
    assert source_edge(STATS, None, _edge(_utc(date(2024, 11, 30))), calendar) == (
        calendar[0].close_utc
    )


def test_statistics_with_no_closed_window_have_no_edge() -> None:
    calendar = [_session(date(2024, 12, 3)), _session(date(2024, 12, 4))]
    assert (
        source_edge(STATS, None, _edge(_utc(date(2024, 12, 4), 12)), calendar) is None
    )


def test_production_shape_each_family_uses_only_its_own_want_end() -> None:
    """Tick want ends 2024-12-31, statistics want ends 2026-11-01, the dataset
    runs to 2026-11-06: statistics reach 2026, the tick tier stops in 2024."""
    days = [date(2026, 11, 2) + timedelta(days=n) for n in range(5)]  # Mon..Fri
    calendar = [_session(day) for day in days]
    edge = _edge(_utc(date(2026, 11, 6)))
    assert source_edge(STATS, date(2026, 11, 1), edge, calendar) == _utc(
        date(2026, 11, 1)
    )
    assert source_edge(TICKS, date(2024, 12, 31), edge, calendar) == _utc(
        date(2024, 12, 31)
    )
    assert source_edge(STATS, None, edge, calendar).year == 2026  # type: ignore[union-attr]


@pytest.mark.parametrize("family", [TICKS, BARS, STATS])
def test_no_dataset_edge_gives_no_source_edge(family: SchemaFamily) -> None:
    assert source_edge(family, date(2024, 12, 31), None, []) is None
