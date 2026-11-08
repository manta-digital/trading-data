"""Source edge per family: the futures counterpart of ``COVERAGE_SOURCE_TABLE``.

Slice 305 D1, D2. ``COVERAGE_SOURCE_TABLE`` (``constants.py``) maps a cagg
granularity to the table that feeds it; it has no futures entries, because a
futures family's ingest tables are not caggs. What a futures surface needs is
instead the family's *source edge*: the last instant the provider can have
published that family's data. :data:`FAMILY_PUBLICATION_LAG` is how many
sessions after session D the provider is still publishing D's data;
:func:`source_edge` turns it, the family's own want end and the dataset's
available end into the edge. No family reads another family's edge or want.

Pure except :func:`family_source_edge`, which reads the calendar only for a
family with a lag.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Final

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_PUBLICATION_SESSIONS,
)
from manta_trading.data.tick.status_reads import Edge, open_calendar
from manta_trading.data.tick.tick_calendar import TickCalendarError
from manta_trading.data.tick.universe import TickUniverseEntry

#: Sessions after D during which the provider still publishes D's data. Keyed
#: by every ledger family (a test pins that); the tick tier and bars are
#: complete when their session closes.
FAMILY_PUBLICATION_LAG: Final[Mapping[SchemaFamily, int]] = {
    SchemaFamily.TICK_TIER: 0,
    SchemaFamily.BAR_1M: 0,
    SchemaFamily.STATISTICS: STATISTICS_PUBLICATION_SESSIONS,
}

#: The calendar read reaches back this far from the dataset edge, widened by
#: the same step until it holds ``lag + 1`` sessions closed by the edge (a
#: holiday run is shorter than a week).
_LOOKBACK_STEP = timedelta(days=7)
#: Past this the calendar is wrong or unpopulated: fail rather than loop.
_MAX_LOOKBACK = timedelta(days=90)


def source_edge(
    family: SchemaFamily,
    family_want_end: date | None,
    dataset_edge: Edge | None,
    calendar: Sequence[Session],
) -> datetime | None:
    """``min(want end, E)``; ``None`` when no dataset edge is known.

    ``E`` is the dataset's available end for a family with no lag. For a family
    with a lag it is the close of the last session in ``calendar`` whose
    publication window (D through the close of the session ``lag`` after D)
    ends at or before the available end. ``calendar`` is the production
    calendar's sessions in order, reaching at least ``lag + 1`` sessions
    closed by the available end; a family with no lag ignores it.
    """
    if dataset_edge is None:
        return None
    lag = FAMILY_PUBLICATION_LAG[family]
    ends = [] if family_want_end is None else [utc_midnight(family_want_end)]
    if lag == 0:
        return min([*ends, dataset_edge.available_end])
    published = [s for s in calendar if s.close_utc <= dataset_edge.available_end]
    if len(published) <= lag:
        return None  # not one session's window has closed yet
    return min([*ends, published[-1 - lag].close_utc])


async def family_source_edge(
    calendar_url: str,
    entry: TickUniverseEntry,
    family: SchemaFamily,
    family_want_end: date | None,
    dataset_edge: Edge | None,
) -> datetime | None:
    """:func:`source_edge` with the calendar read it needs (blocking read off
    the loop). A family with no lag, or no dataset edge, reads nothing."""
    calendar: list[Session] = []
    if dataset_edge is not None and FAMILY_PUBLICATION_LAG[family]:
        calendar = await asyncio.to_thread(
            _calendar_to, calendar_url, entry, family, dataset_edge
        )
    return source_edge(family, family_want_end, dataset_edge, calendar)


def _calendar_to(
    calendar_url: str, entry: TickUniverseEntry, family: SchemaFamily, edge: Edge
) -> list[Session]:
    """Blocking: sessions up to the available end, enough to hold the lag.

    The read stops at the calendar's populated end when the dataset reports an
    edge past it: no window is then judged closed that the calendar cannot show.
    """
    calendar_id = calendar_for_product(entry.product)
    with open_calendar(calendar_url, calendar_id) as calendar:
        _, last_close = calendar.populated_span()
        if last_close is None:
            raise TickCalendarError(f"calendar {calendar_id} has no sessions")
        end = min(edge.available_end, last_close)
        reach = _LOOKBACK_STEP
        while reach <= _MAX_LOOKBACK:
            found = calendar.sessions_between(end - reach, end)
            closed = [s for s in found if s.close_utc <= end]
            if len(closed) > FAMILY_PUBLICATION_LAG[family]:
                return closed
            reach += _LOOKBACK_STEP
    raise TickCalendarError(
        f"{calendar_id}: no {FAMILY_PUBLICATION_LAG[family] + 1} sessions "
        f"closed within {_MAX_LOOKBACK.days} days of {end.isoformat()}"
    )
