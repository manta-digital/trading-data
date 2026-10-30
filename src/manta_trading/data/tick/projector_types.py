"""The contract between the ingest worker and a schema's projector (slice 300, D2).

A projector turns one verified archive unit's file into rows in its own tables,
inside the worker's open transaction. The worker knows only this contract: it
looks a projector up by the unit's schema, and never names a table.

Kept apart from the registry (``projectors.py``) so a projector module can
import :class:`Loaded` without importing the registry that imports it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import psycopg

from manta_trading.data.tick.provider import ITickFileReader

if TYPE_CHECKING:
    from manta_trading.data.tick.ingest_plan import UnitIngestPlan

Cursor = psycopg.Cursor[Any]

#: Receives the row count of each ``COPY`` batch (234). Rows are inside the
#: unit's open transaction, so a count is a liveness signal, not a commit.
BatchProgress = Callable[[int], None]


@dataclass
class Loaded:
    """What a projector's ``load`` counted, for the checks and ``finish``.

    ``bookkeeping`` is the projector's own hand-off from ``load`` to ``finish``
    (for ``tick_trade``, the ledger rows); the worker never reads it.
    """

    decoded: int = 0
    first_ns: int | None = None
    last_ns: int | None = None
    decode_seconds: float = 0.0
    bookkeeping: object | None = None


class IUnitProjector(Protocol):
    def load(
        self,
        cur: Cursor,
        plan: UnitIngestPlan,
        path: Path,
        reader: ITickFileReader,
        progress: BatchProgress | None,
    ) -> Loaded:
        """Write the unit's records; return what was decoded."""
        ...

    def stored_count(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> int:
        """Rows the unit now holds, bounded by its event times; 0 with none."""
        ...

    def finish(self, cur: Cursor, unit_id: int, loaded: Loaded) -> None:
        """Write the projector's bookkeeping once the counts agree."""
        ...

    def delete_unit(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> None:
        """Delete an ingested unit's rows when a higher-ranked unit supersedes it."""
        ...
