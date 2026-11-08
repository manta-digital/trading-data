"""The per-family freshness lines of ``mt data overview`` (slice 305 D7).

``futures ticks`` (230) shows the newest stored tick; this adds the newest
``ohlcv-1m`` bar and the newest statistics session, each from its ledger in one
read on the composed connection. A family no product wants prints no line. A
tick outage is the line's own text, never a failed overview (230 TD8).
"""

from __future__ import annotations

from typing import Any

from manta_trading.cli.overview_types import SourceFreshness
from manta_trading.data.tick.constants import BAR_SOURCE_NAME, STATISTICS_SOURCE_NAME
from manta_trading.data.tick.line_state import TickUnavailable, unavailable_text
from manta_trading.data.tick.roll_types import utc_from_ns
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import (
    newest_bar_ns,
    newest_statistics_session,
)
from manta_trading.data.tick.tick_status_scope import family_wants


def read_futures_sources(settings: Any) -> list[SourceFreshness]:
    """The ``ohlcv-1m`` and statistics lines, for the families some product wants."""
    from manta_trading.cli.tick_composed import read_line
    from manta_trading.data.tick.universe import TICK_UNIVERSE

    wanted = {w.family for entry in TICK_UNIVERSE for w in family_wants(entry)}
    lines: list[SourceFreshness] = []
    if SchemaFamily.BAR_1M in wanted:
        bar = read_line(settings, newest_bar_ns)
        lines.append(
            _unavailable(BAR_SOURCE_NAME, bar)
            if isinstance(bar, TickUnavailable)
            else SourceFreshness(
                BAR_SOURCE_NAME, None if bar is None else utc_from_ns(bar)
            )
        )
    if SchemaFamily.STATISTICS in wanted:
        session = read_line(settings, newest_statistics_session)
        lines.append(
            _unavailable(STATISTICS_SOURCE_NAME, session)
            if isinstance(session, TickUnavailable)
            else SourceFreshness(STATISTICS_SOURCE_NAME, None, None, session)
        )
    return lines


def _unavailable(name: str, why: TickUnavailable) -> SourceFreshness:
    return SourceFreshness(name, None, unavailable_text(why))
