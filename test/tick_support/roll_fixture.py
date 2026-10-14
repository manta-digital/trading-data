"""Load slice 229's recorded production fixtures as resolver inputs.

``scripts/record_229_roll_fixture.py`` wrote them; see
``user/notes/229-roll-fixture-record.md``. No database, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.roll_types import (
    ContractDefinition,
    RollInputs,
    SessionVolume,
)
from manta_trading.data.tick.tick_status import TickSessionStatus

ROLL_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tick" / "roll"


@dataclass(frozen=True)
class ProviderInterval:
    """Databento maps a continuous symbol to ``instrument_id`` on dates
    ``[start, end)``."""

    start: date
    end: date
    instrument_id: int


def _read(product: str, name: str) -> dict[str, Any]:
    path = ROLL_FIXTURES / f"{product.lower()}_{name}.json"
    body: dict[str, Any] = json.loads(path.read_text())
    return body


def recorded_inputs(product: str) -> RollInputs:
    definitions = _read(product, "definitions")
    volumes = _read(product, "volumes")
    calendar_id = calendar_for_product(product)
    sessions = tuple(
        Session(
            calendar_id,
            date.fromisoformat(row["session_date"]),
            datetime.fromisoformat(row["open_utc"]),
            datetime.fromisoformat(row["close_utc"]),
        )
        for row in _read(product, "calendar")["rows"]
    )
    return RollInputs(
        sessions=sessions,
        contracts=tuple(ContractDefinition(**row) for row in definitions["rows"]),
        volumes=tuple(
            SessionVolume(
                **(row | {"session_date": date.fromisoformat(row["session_date"])})
            )
            for row in volumes["rows"]
        ),
        statuses={
            date.fromisoformat(day): TickSessionStatus(status)
            for day, status in volumes["statuses"].items()
        },
        definitions_as_of=date.fromisoformat(definitions["definitions_as_of"]),
        calendar_horizon=sessions[-1].session_date,
    )


def provider_intervals(product: str) -> dict[str, tuple[ProviderInterval, ...]]:
    """Databento's intervals per continuous symbol (``{product}.v.0``, …)."""
    body = _read(product, "databento")
    return {
        symbol: tuple(
            ProviderInterval(
                date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]),
                row["instrument_id"],
            )
            for row in rows
        )
        for symbol, rows in body["rows"].items()
    }


def provider_span(product: str) -> tuple[date, date]:
    """The resolved span, ``[start, end)``."""
    body = _read(product, "databento")
    return date.fromisoformat(body["start"]), date.fromisoformat(body["end_exclusive"])
