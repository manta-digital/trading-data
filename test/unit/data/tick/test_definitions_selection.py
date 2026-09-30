"""The definitions phase selects definition units only (slice 224, TD5).

The phase is the one place the acquisition pass writes tier-adjacent data, so
its bounded exception is enforced here: the selection is keyed on
``TickSchema.DEFINITION`` and on nothing wider.
"""

from __future__ import annotations

from typing import Any

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.manifest_reads import verified_definition_units


class _Cursor:
    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._sink = sink

    async def __aenter__(self) -> _Cursor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self._sink.append((query, params))

    async def fetchall(self) -> list[dict[str, Any]]:
        return []


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self, **_: Any) -> _Cursor:
        return _Cursor(self.executed)


async def test_selection_is_verified_definition_units_and_no_other_schema() -> None:
    conn = _Conn()
    assert await verified_definition_units(conn) == []  # type: ignore[arg-type]
    ((query, params),) = conn.executed
    assert "r.schema = %s" in query
    assert params[0] == UnitState.VERIFIED.value
    assert params[1] == TickSchema.DEFINITION.value
    schemas = {s.value for s in TickSchema}
    scalars = [p for p in params if isinstance(p, str)]
    assert {p for p in scalars if p in schemas} == {TickSchema.DEFINITION.value}
