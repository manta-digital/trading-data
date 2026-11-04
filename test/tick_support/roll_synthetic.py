"""Synthetic resolver inputs for slice 229's pure tests.

Sessions are weekdays: the session dated D opens at 22:00 UTC the day before
and closes at 21:00 UTC on D (CME's shape without holidays). A contract is
listed from a session (activation = that session's open) and expires at
13:30 UTC on its expiry session, as ES does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.roll_types import (
    ContractDefinition,
    RollInputs,
    SessionOpenInterest,
    SessionVolume,
)
from manta_trading.data.tick.tick_status import TickSessionStatus

CALENDAR_ID = "SYNTHETIC"
DEFINITIONS_AS_OF = date(2024, 8, 30)
DEFINITION_UNIT = 7
_DAY = timedelta(days=1)


def weekdays(first: date, count: int) -> list[date]:
    days: list[date] = []
    day = first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += _DAY
    return days


def session(day: date) -> Session:
    opened = datetime.combine(day - _DAY, time(22), UTC)
    return Session(CALENDAR_ID, day, opened, datetime.combine(day, time(21), UTC))


def sessions(first: date, count: int) -> tuple[Session, ...]:
    return tuple(session(day) for day in weekdays(first, count))


def contract(
    instrument_id: int, symbol: str, listed_from: date, expires: date
) -> ContractDefinition:
    return ContractDefinition(
        instrument_id=instrument_id,
        raw_symbol=symbol,
        activation_ns=utc_ns(session(listed_from).open_utc),
        expiration_ns=utc_ns(datetime.combine(expires, time(13, 30), UTC)),
        unit_id=DEFINITION_UNIT,
    )


def unit_ids(day: date) -> tuple[int, int]:
    """The two day units a session's ledger rows come from (it touches two days)."""
    base = day.toordinal() * 10
    return base, base + 1


def volume_rows(volumes: Mapping[tuple[date, int], int]) -> tuple[SessionVolume, ...]:
    """Each (session, instrument) volume split across the session's two units."""
    rows: list[SessionVolume] = []
    for (day, instrument), volume in volumes.items():
        first, second = unit_ids(day)
        rows.append(SessionVolume(day, instrument, volume // 2, first))
        rows.append(SessionVolume(day, instrument, volume - volume // 2, second))
    return tuple(rows)


#: The unit a session's open-interest record comes from (one per session).
def interest_unit(day: date) -> int:
    return day.toordinal() * 10 + 5


def interest_rows(
    interest: Mapping[tuple[date, int], int],
) -> tuple[SessionOpenInterest, ...]:
    return tuple(
        SessionOpenInterest(day, instrument, value, interest_unit(day))
        for (day, instrument), value in interest.items()
    )


def inputs(
    run: tuple[Session, ...],
    contracts: Iterable[ContractDefinition],
    volumes: Mapping[tuple[date, int], int] | None = None,
    statuses: Mapping[date, TickSessionStatus] | None = None,
    *,
    bar_volumes: Mapping[tuple[date, int], int] | None = None,
    bar_statuses: Mapping[date, TickSessionStatus] | None = None,
    interest: Mapping[tuple[date, int], int] | None = None,
    statistics_statuses: Mapping[date, TickSessionStatus] | None = None,
) -> RollInputs:
    """Every session complete unless ``statuses`` says otherwise.

    The bar family and the statistics are read only when their arguments are
    given: their statuses default to every session complete then, as the tick
    tier's do, and to "not read" (empty) otherwise.
    """
    complete = {s.session_date: TickSessionStatus.COMPLETE for s in run}
    return RollInputs(
        sessions=run,
        contracts=tuple(contracts),
        volumes=volume_rows(volumes or {}),
        statuses=complete | dict(statuses or {}),
        definitions_as_of=DEFINITIONS_AS_OF,
        calendar_horizon=run[-1].session_date,
        bar_volumes=volume_rows(bar_volumes or {}),
        bar_statuses=(
            {}
            if bar_volumes is None and bar_statuses is None
            else complete | dict(bar_statuses or {})
        ),
        open_interest=interest_rows(interest or {}),
        statistics_statuses=(
            {}
            if interest is None and statistics_statuses is None
            else complete | dict(statistics_statuses or {})
        ),
    )


def symbols(answers: Iterable[object]) -> list[str]:
    """``raw_symbol`` per answer, ``"-"`` for an unresolved one."""
    return [getattr(a, "raw_symbol", "-") for a in answers]
