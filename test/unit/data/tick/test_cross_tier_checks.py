"""``traded_vs_cleared``: which sessions are checked and how verdicts are counted
(slice 305, D4). 303's ``volume_check`` is stubbed; its own tests cover it."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick import cross_tier_checks
from manta_trading.data.tick.cross_tier_checks import (
    INACTIVE_CROSS_TIER_CHECKS,
    traded_vs_cleared,
)
from manta_trading.data.tick.cross_tier_result import CrossTierResult
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_reads import VolumeClass, VolumeVerdict
from manta_trading.data.tick.tick_status import SessionVerdict, TickSessionStatus
from manta_trading.data.tick.tick_status_scope import ProductScope
from manta_trading.data.tick.universe import TICK_UNIVERSE

S = TickSessionStatus
TICKS, BARS, STATS = (
    SchemaFamily.TICK_TIER,
    SchemaFamily.BAR_1M,
    SchemaFamily.STATISTICS,
)
ENTRY = replace(TICK_UNIVERSE[0], history=())
DAYS = [date(2024, 12, 2) + timedelta(days=n) for n in range(4)]  # Mon..Thu
WANTED = (
    datetime(2024, 12, 2, tzinfo=UTC),
    datetime(2024, 12, 7, tzinfo=UTC),
)


def _scope(*statuses: TickSessionStatus) -> ProductScope:
    sessions = [
        Session(
            "CME_EQUITY",
            day,
            datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=1),
            datetime(day.year, day.month, day.day, 22, tzinfo=UTC),
        )
        for day in DAYS[: len(statuses)]
    ]
    verdicts = [
        SessionVerdict(s.session_date, st, "available", ())
        for s, st in zip(sessions, statuses, strict=True)
    ]
    return ProductScope(DAYS[0], WANTED[1], WANTED, sessions, verdicts)


class _VolumeCheck:
    """Records the sessions of each call and answers a fixed verdict list."""

    def __init__(self, answers: dict[SchemaFamily, list[VolumeClass]]) -> None:
        self.answers = answers
        self.calls: list[tuple[SchemaFamily, list[date]]] = []

    async def __call__(
        self, conn: Any, shape: Any, family: SchemaFamily, product: str, sessions: Any
    ) -> tuple[VolumeVerdict, ...]:
        self.calls.append((family, [s.session_date for s in sessions]))
        return tuple(
            VolumeVerdict(DAYS[0], 1, "ES", family, 10, 10, verdict)
            for verdict in self.answers.get(family, [])
        )


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> _VolumeCheck:
    check = _VolumeCheck({})
    monkeypatch.setattr(cross_tier_checks, "volume_check", check)
    return check


async def test_no_statistics_want_means_no_check(stub: _VolumeCheck) -> None:
    assert await traded_vs_cleared(None, ENTRY, {TICKS: _scope(S.COMPLETE)}) == ()  # type: ignore[arg-type]
    assert stub.calls == []


async def test_statistics_with_no_volume_family_means_no_check(
    stub: _VolumeCheck,
) -> None:
    assert await traded_vs_cleared(None, ENTRY, {STATS: _scope(S.COMPLETE)}) == ()  # type: ignore[arg-type]
    assert stub.calls == []


async def test_each_volume_family_is_checked_over_sessions_complete_in_both(
    stub: _VolumeCheck,
) -> None:
    scopes = {
        TICKS: _scope(S.COMPLETE, S.COMPLETE, S.MISSING, S.COMPLETE),
        BARS: _scope(S.COMPLETE, S.COMPLETE, S.COMPLETE, S.COMPLETE),
        STATS: _scope(S.COMPLETE, S.PENDING, S.COMPLETE, S.COMPLETE),
    }
    results = await traded_vs_cleared(None, ENTRY, scopes)  # type: ignore[arg-type]
    assert [r.family for r in results] == [TICKS, BARS]
    # Ticks: Mon, Thu (Tue pending in statistics, Wed missing in ticks).
    assert stub.calls == [
        (TICKS, [DAYS[0], DAYS[3]]),
        (BARS, [DAYS[0], DAYS[2], DAYS[3]]),
    ]


async def test_verdicts_are_counted_per_class_including_over(
    stub: _VolumeCheck,
) -> None:
    stub.answers[TICKS] = [
        VolumeClass.AGREE,
        VolumeClass.UNDER,
        VolumeClass.UNDER,
        VolumeClass.OVER,
    ]
    scopes = {TICKS: _scope(S.COMPLETE), STATS: _scope(S.COMPLETE)}
    (result,) = await traded_vs_cleared(None, ENTRY, scopes)  # type: ignore[arg-type]
    assert result == CrossTierResult("traded_vs_cleared", TICKS, 1, 2, 1, 0)


def test_the_result_has_one_count_per_volume_class() -> None:
    counts = {f.name for f in fields(CrossTierResult)} - {"check", "family"}
    assert counts == {c.value for c in VolumeClass}


def test_the_ohlcv_1s_check_is_listed_as_not_active_with_its_reason() -> None:
    assert INACTIVE_CROSS_TIER_CHECKS == {
        "ohlcv_1s_minute_sum": "needs the ohlcv-1s family"
    }
