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


def test_every_bar_column_but_unit_id_is_hashed() -> None:
    from manta_trading.data.tick.bar_constants import BAR_1M_COLUMNS

    assert fp.bar_hashed_columns() == [c for c in BAR_1M_COLUMNS if c != "unit_id"]
    row_part = fp.BAR_FINGERPRINT_SQL.as_string(None).split("ROW(")[1].split(")")[0]
    assert '"unit_id"' not in row_part
    assert '"close"' in row_part
    assert 'FROM "tick_ohlcv_1m"' in fp.BAR_FINGERPRINT_SQL.as_string(None)


def test_the_bar_hash_orders_by_the_bar_key() -> None:
    rendered = fp.BAR_FINGERPRINT_SQL.as_string(None)
    assert 'ORDER BY "instrument_id", "ts_event"' in rendered


def test_every_statistics_column_but_unit_id_is_hashed() -> None:
    from manta_trading.data.tick.statistics_constants import STATISTICS_COLUMNS

    assert fp.statistics_hashed_columns() == [
        c for c in STATISTICS_COLUMNS if c != "unit_id"
    ]
    rendered = fp.STATISTICS_FINGERPRINT_SQL.as_string(None)
    row_part = rendered.split("ROW(")[1].split(")")[0]
    assert '"unit_id"' not in row_part
    assert '"session_date"' in row_part
    assert 'FROM "tick_statistics"' in rendered


def test_the_statistics_hash_orders_by_its_key_and_groups_by_receive_time() -> None:
    rendered = fp.STATISTICS_FINGERPRINT_SQL.as_string(None)
    assert (
        'ORDER BY "instrument_id", "stat_type", "ts_recv", "sequence", "record_ordinal"'
        in rendered
    )
    assert '"ts_recv" / ' in rendered
    assert '"ts_event" / ' in fp.BAR_FINGERPRINT_SQL.as_string(None)
