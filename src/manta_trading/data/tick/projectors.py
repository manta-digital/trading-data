"""Which schemas ingest can project, and the projector for each (slice 300, D2).

The keys are the ingest selection set: ``select_units`` and ``build_plan`` take
only units whose schema is a key. ``definition`` is not here: the acquisition
pass projects definitions itself (D3). 301 adds the bar schemas with their own
projector.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.projector_types import IUnitProjector
from manta_trading.data.tick.tick_trade_projector import TICK_TRADE

INGEST_PROJECTORS: Final[Mapping[TickSchema, IUnitProjector]] = {
    TickSchema.TRADES: TICK_TRADE,
    TickSchema.TBBO: TICK_TRADE,
}


class NoProjectorError(LookupError):
    """A unit's schema has no ingest projector (no default projector exists)."""


def projector_for(schema: TickSchema) -> IUnitProjector:
    try:
        return INGEST_PROJECTORS[schema]
    except KeyError as exc:
        raise NoProjectorError(f"schema {schema} has no ingest projector") from exc
