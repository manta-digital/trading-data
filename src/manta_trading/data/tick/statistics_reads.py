"""Reads of the stored statistics (slice 303, D5).

``resolved`` is the only place a statistic value is chosen. Records of one
(contract, statistic, session) arrive in many units and may revise each other;
the value read is the latest by receive time, then ``sequence``, then
``record_ordinal``, over the current units of the shape. Resolution is at read
time, not in a stored table: corrections arrive in later units, and a stored
winner would need rewriting on every ingest. SQL is written here from code
constants; every value is bound.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Final

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import VOLUME_FAMILIES, SchemaFamily
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_TABLE,
    StatType,
    StatUpdateAction,
)
from manta_trading.data.tick.statistics_status import (
    listed_instruments,
    outrights,
    statistics_verdicts,
)
from manta_trading.data.tick.status_reads import (
    SHAPE_WHERE,
    Shape,
    ledger_units,
    ledger_volumes,
    session_verdicts,
)
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.universe import TickUniverseEntry


@dataclass(frozen=True)
class ResolvedStatistic:
    """The winning record of one (contract, statistic, session)."""

    instrument_id: int
    stat_type: int
    session_date: date
    price: int | None
    quantity: int | None
    unit_id: int


#: ``DISTINCT ON`` keeps the first row of each key under this order: the latest
#: record. A winning ``DELETE`` is dropped by the outer query, so the key has no
#: value, whatever earlier records said.
_RESOLVED = (
    "SELECT instrument_id, stat_type, session_date, price, quantity, unit_id"
    " FROM ("
    "SELECT DISTINCT ON (s.instrument_id, s.stat_type, s.session_date)"
    " s.instrument_id, s.stat_type, s.session_date, s.update_action,"
    " s.price, s.quantity, s.unit_id"
    f" FROM {STATISTICS_TABLE} s JOIN tick_archive_unit u ON u.unit_id = s.unit_id"
    " JOIN tick_request r ON r.request_id = u.request_id"
    f" WHERE {SHAPE_WHERE}"
    " AND s.stat_type = ANY(%(stat_types)s)"
    " AND s.session_date BETWEEN %(first)s AND %(last)s"
    " ORDER BY s.instrument_id, s.stat_type, s.session_date,"
    " s.ts_recv DESC, s.sequence DESC, s.record_ordinal DESC"
    ") latest WHERE update_action <> %(delete)s"
    " ORDER BY session_date, instrument_id, stat_type"
)


async def resolved(
    conn: Conn,
    shape: Shape,
    stat_types: Collection[StatType],
    first: date,
    last: date,
) -> tuple[ResolvedStatistic, ...]:
    """One row per (instrument, stat type, session) in ``[first, last]``.

    Over the current units of ``shape`` (superseded and reopened units never
    count). Records with no session never appear.
    """
    cursor = await conn.execute(
        _RESOLVED,
        {
            **shape.params(SchemaFamily.STATISTICS),
            "stat_types": sorted(int(stat_type) for stat_type in stat_types),
            "first": first,
            "last": last,
            "delete": int(StatUpdateAction.DELETE),
        },
    )
    return tuple(ResolvedStatistic(*row) for row in await cursor.fetchall())


class VolumeClass(StrEnum):
    """How a session's traded volume V compares with CME's cleared volume C (D8)."""

    AGREE = "agree"
    UNDER = "under"  # V < C: normal, cleared volume includes block/EFP/EFR trades
    OVER = "over"  # V > C: more traded than cleared; a defect
    NO_TRADES = "no_trades"  # V = 0 and C > 0: cleared volume with nothing traded


#: The classes a coverage run reports as findings (exit 3); ``under`` is not.
VOLUME_FINDINGS: Final[frozenset[VolumeClass]] = frozenset(
    {VolumeClass.OVER, VolumeClass.NO_TRADES}
)


def classify_volume(cleared: int, traded: int) -> VolumeClass:
    """Pure: ``agree`` when equal (zeros included), ``over`` above C, ``no_trades``
    for nothing traded against a positive C, else ``under``."""
    if traded == cleared:
        return VolumeClass.AGREE
    if traded > cleared:
        return VolumeClass.OVER
    if traded == 0:
        return VolumeClass.NO_TRADES
    return VolumeClass.UNDER


@dataclass(frozen=True)
class VolumeVerdict:
    """One listed outright in one session, against one volume family."""

    session_date: date
    instrument_id: int
    raw_symbol: str | None
    family: SchemaFamily
    cleared: int
    traded: int
    verdict: VolumeClass

    @property
    def ratio(self) -> float | None:
        """V / C; ``None`` when C is 0 (there is nothing to compare against)."""
        return None if self.cleared == 0 else self.traded / self.cleared


def settle_no_trades(verdicts: Sequence[VolumeVerdict]) -> tuple[VolumeVerdict, ...]:
    """Keep ``no_trades`` only for a session where nothing at all traded.

    A deferred contract's cleared volume is mostly spread-leg volume, which no
    outright trade record carries (ESZ5 cleared 138 against 0 traded, 2024-11-04,
    while the front month agreed within 5%; slice 303 task 8). So V = 0 beside
    C > 0 is a finding only when every contract in the session traded nothing;
    otherwise the contract is ``under``.
    """
    traded_days = {v.session_date for v in verdicts if v.traded > 0}
    return tuple(
        replace(v, verdict=VolumeClass.UNDER)
        if v.verdict is VolumeClass.NO_TRADES and v.session_date in traded_days
        else v
        for v in verdicts
    )


async def checkable_sessions(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    sessions: Sequence[Session],
    family: SchemaFamily,
) -> list[Session]:
    """The sessions load-complete both in ``statistics`` and in ``family``.

    A session incomplete in either is skipped: its cleared volume or its trades
    are not all there, so a comparison would be noise (D8).
    """
    if family not in VOLUME_FAMILIES:
        raise ValueError(f"family {family} has no traded volume to check")
    statistics = await statistics_verdicts(calendar_url)(
        conn, entry, sessions, SchemaFamily.STATISTICS
    )
    traded = await session_verdicts(conn, entry, sessions, family)
    return [
        session
        for session, in_statistics, in_family in zip(
            sessions, statistics, traded, strict=True
        )
        if in_statistics.status is TickSessionStatus.COMPLETE
        and in_family.status is TickSessionStatus.COMPLETE
    ]


async def volume_check(
    conn: Conn,
    shape: Shape,
    family: SchemaFamily,
    product: str,
    sessions: Sequence[Session],
) -> tuple[VolumeVerdict, ...]:
    """Compare traded volume with resolved cleared volume per listed outright.

    One resolved read of cleared volume and one ledger read of ``family``
    (through ``FAMILY_LEDGER``), bounded by the sessions' dates. Callers pass
    sessions that are load-complete in both (``checkable_sessions``). A contract
    with no resolved cleared volume is not compared: that gap is the status
    section's content finding.
    """
    if family not in VOLUME_FAMILIES:
        raise ValueError(f"family {family} has no traded volume to check")
    if not sessions:
        return ()
    first = min(s.session_date for s in sessions)
    last = max(s.session_date for s in sessions)
    cleared = {
        (row.instrument_id, row.session_date): row.quantity
        for row in await resolved(conn, shape, [StatType.CLEARED_VOLUME], first, last)
    }
    traded = await ledger_volumes(conn, shape, family, first, last)
    definitions = await outrights(conn, product)
    verdicts = []
    for session in sessions:
        for instrument_id in listed_instruments(definitions, session):
            quantity = cleared.get((instrument_id, session.session_date))
            if quantity is None:
                continue
            volume = traded.get((instrument_id, session.session_date), 0)
            verdicts.append(
                VolumeVerdict(
                    session.session_date,
                    instrument_id,
                    definitions[instrument_id][0],
                    family,
                    quantity,
                    volume,
                    classify_volume(quantity, volume),
                )
            )
    return settle_no_trades(verdicts)


async def ledger_counts_by_type(
    conn: Conn,
    shape: Shape,
    stat_types: Collection[StatType],
    first: date,
    last: date,
) -> dict[tuple[int, date, int], int]:
    """``record_count`` per (instrument, session, stat type) from the ledger over
    the shape's current units."""
    cursor = await conn.execute(
        "SELECT l.instrument_id, l.session_date, l.stat_type, sum(l.record_count)"
        f" FROM {ledger_units(SchemaFamily.STATISTICS)} WHERE {SHAPE_WHERE}"
        " AND l.stat_type = ANY(%(stat_types)s)"
        " AND l.session_date BETWEEN %(first)s AND %(last)s GROUP BY 1, 2, 3",
        shape.params(SchemaFamily.STATISTICS)
        | {
            "stat_types": sorted(int(t) for t in stat_types),
            "first": first,
            "last": last,
        },
    )
    return {(i, d, t): int(n) for i, d, t, n in await cursor.fetchall()}
