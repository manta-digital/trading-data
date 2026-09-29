"""Column contract against the real DBN layouts (slice 222 TD3, TD5).

The fixtures are the real provider format: a renamed or removed DBN field
fails here before any writer depends on it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.storage_columns import (
    TICK_DEFINITION_COLUMNS,
    TICK_DEFINITION_DERIVED_COLUMNS,
    TICK_TRADE_BBO_COLUMNS,
    TICK_TRADE_COLUMNS,
    TICK_TRADE_DERIVED_COLUMNS,
    TICK_TRADE_KEY,
)

FIXTURES = Path(__file__).parents[3] / "fixtures" / "databento"

#: DBN framing, never stored (TD3).
_FRAMING_FIELDS = frozenset({"length", "rtype"})


def _fields(name: str) -> tuple[str, ...]:
    file = DbnFileReader().open_file(FIXTURES / f"test_data.{name}.dbn.zst")
    names = next(file.iter_batches()).records.dtype.names
    assert names is not None
    return names


@pytest.mark.parametrize("fixture", ["trades.v2", "trades.v3"])
def test_trade_body_fields_exist_in_trades_layout(fixture: str) -> None:
    body = set(TICK_TRADE_COLUMNS) - set(TICK_TRADE_BBO_COLUMNS)
    assert body <= set(_fields(fixture))


def test_every_trade_field_exists_in_tbbo_layout() -> None:
    assert set(TICK_TRADE_COLUMNS) <= set(_fields("tbbo.v3"))


@pytest.mark.parametrize("fixture", ["trades.v2", "trades.v3", "tbbo.v3"])
def test_every_stored_body_field_is_mapped(fixture: str) -> None:
    assert set(_fields(fixture)) - _FRAMING_FIELDS <= set(TICK_TRADE_COLUMNS)


def test_definition_fields_exist_in_definition_layout() -> None:
    assert set(TICK_DEFINITION_COLUMNS) <= set(_fields("definition.v3"))


def test_bbo_columns_are_mapped_trade_columns() -> None:
    assert set(TICK_TRADE_BBO_COLUMNS) <= set(TICK_TRADE_COLUMNS.values())


def test_trade_key_names_table_columns() -> None:
    columns = (*TICK_TRADE_COLUMNS.values(), *TICK_TRADE_DERIVED_COLUMNS)
    assert set(TICK_TRADE_KEY) <= set(columns)


@pytest.mark.parametrize(
    ("mapped", "derived"),
    [
        (TICK_TRADE_COLUMNS, TICK_TRADE_DERIVED_COLUMNS),
        (TICK_DEFINITION_COLUMNS, TICK_DEFINITION_DERIVED_COLUMNS),
    ],
    ids=["tick_trade", "tick_definition"],
)
def test_no_column_name_repeats(
    mapped: Mapping[str, str], derived: tuple[str, ...]
) -> None:
    columns = [*mapped.values(), *derived]
    assert len(columns) == len(set(columns))
