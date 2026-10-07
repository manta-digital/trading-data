"""The fingerprint's column list follows ``storage_columns.py`` (slice 228 TD3)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drill_228_fingerprint as fp  # noqa: E402

from manta_trading.data.tick.storage_columns import (  # noqa: E402
    TICK_TRADE_COLUMNS,
    TICK_TRADE_DERIVED_COLUMNS,
)


def test_every_contract_column_but_unit_id_is_hashed() -> None:
    expected = [*TICK_TRADE_COLUMNS.values(), *TICK_TRADE_DERIVED_COLUMNS]
    assert fp.hashed_columns() == [c for c in expected if c != "unit_id"]
    assert "sequence_ordinal" in fp.hashed_columns()
    assert "bid_px_00" in fp.hashed_columns()


def test_unit_id_is_not_in_the_row_hash() -> None:
    row_part = fp.FINGERPRINT_SQL.as_string(None).split("ROW(")[1].split(")")[0]
    assert '"unit_id"' not in row_part


def test_a_new_contract_column_is_hashed_automatically() -> None:
    widened = {**TICK_TRADE_COLUMNS, "new_field": "new_column"}
    rendered = fp.fingerprint_sql(fp.hashed_columns(widened)).as_string(None)
    assert '"new_column"' in rendered.split("ROW(")[1]
