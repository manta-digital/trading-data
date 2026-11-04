"""The statistics side of ``coverage`` (slice 303, D8): counts and volume.

For each session dated in the range: its status over the publication window
(``statistics_verdicts``), the ledger's record count against the stored rows per
required stat type, and the cross-tier volume check per contract and family.
Returns the pieces; ``tick_coverage`` assembles the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.coverage_rows import CoverageSession, Mismatch
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.raw_reads import raw_statistics_counts
from manta_trading.data.tick.schema_families import VOLUME_FAMILIES, SchemaFamily
from manta_trading.data.tick.statistics_constants import REQUIRED_STAT_TYPES
from manta_trading.data.tick.statistics_reads import (
    VolumeVerdict,
    checkable_sessions,
    ledger_counts_by_type,
    volume_check,
)
from manta_trading.data.tick.statistics_status import statistics_verdicts
from manta_trading.data.tick.status_reads import shape_of
from manta_trading.data.tick.tick_status import SessionVerdict
from manta_trading.data.tick.tick_status_scope import family_wants
from manta_trading.data.tick.universe import TickUniverseEntry

Counts = Mapping[tuple[int, date, int], int]
"""Records per (instrument, session, stat type)."""


def _session_row(v: SessionVerdict, ledger: Counts, raw: Counts) -> CoverageSession:
    day = v.session_date
    keys = sorted({(i, t) for i, d, t in (*ledger, *raw) if d == day})
    pairs = [
        (i, t, ledger.get((i, day, t), 0), raw.get((i, day, t), 0)) for i, t in keys
    ]
    return CoverageSession(
        session_date=day,
        condition=v.condition,
        status=v.status.value,
        unit_ids=tuple(sorted({f.unit.unit_id for f in v.days if f.unit})),
        ledger=sum(a for _, _, a, _ in pairs),
        raw=sum(b for _, _, _, b in pairs),
        mismatches=tuple(
            Mismatch(i, a, b, stat_type=t) for i, t, a, b in pairs if a != b
        ),
    )


async def _volume_checks(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    sessions: Sequence[Session],
) -> tuple[VolumeVerdict, ...]:
    """The check against each volume family the entry wants (tier and bars)."""
    wanted = [w.family for w in family_wants(entry) if w.family in VOLUME_FAMILIES]
    verdicts: list[VolumeVerdict] = []
    for family in wanted:
        checkable = await checkable_sessions(
            conn, calendar_url, entry, sessions, family
        )
        verdicts += await volume_check(
            conn, shape_of(entry), family, entry.product, checkable
        )
    return tuple(verdicts)


async def statistics_sections(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    sessions: Sequence[Session],
    start: date,
    end: date,
) -> tuple[tuple[CoverageSession, ...], tuple[VolumeVerdict, ...]]:
    """The session rows and the volume checks for sessions dated ``[start, end)``."""
    verdicts = await statistics_verdicts(calendar_url)(
        conn, entry, sessions, SchemaFamily.STATISTICS
    )
    last = end - timedelta(days=1)
    shape = shape_of(entry)
    ledger = await ledger_counts_by_type(conn, shape, REQUIRED_STAT_TYPES, start, last)
    raw = await raw_statistics_counts(conn, shape, REQUIRED_STAT_TYPES, start, last)
    rows = tuple(_session_row(v, ledger, raw) for v in verdicts)
    return rows, await _volume_checks(conn, calendar_url, entry, sessions)
