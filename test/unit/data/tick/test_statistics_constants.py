"""Statistics storage constants (slice 303, D2, D4)."""

from __future__ import annotations

import databento_dbn

from manta_trading.data.tick.statistics_constants import (
    REFERENCED_STAT_TYPES,
    REQUIRED_STAT_TYPES,
    STATISTICS_COLUMNS,
    STATISTICS_KEY,
    STATISTICS_LEDGER_KEY,
    STATISTICS_ORDER_BY,
    STATISTICS_PUBLICATION_SESSIONS,
    STATISTICS_SEGMENT_BY,
    StatType,
)


def test_stat_types_are_the_dbn_values() -> None:
    assert [(t.name, t.value) for t in StatType] == [
        ("SETTLEMENT_PRICE", 3),
        ("CLEARED_VOLUME", 6),
        ("OPEN_INTEREST", 9),
    ]


def test_stat_types_match_the_sdk() -> None:
    for stat_type in StatType:
        assert stat_type.value == int(getattr(databento_dbn.StatType, stat_type.name))


def test_referenced_and_required_types_are_the_three() -> None:
    assert REFERENCED_STAT_TYPES == set(StatType)
    assert REQUIRED_STAT_TYPES == REFERENCED_STAT_TYPES


def test_publication_window_is_one_session() -> None:
    assert STATISTICS_PUBLICATION_SESSIONS == 1


def test_keys_and_layout_name_real_columns() -> None:
    assert len(STATISTICS_COLUMNS) == len(set(STATISTICS_COLUMNS))
    for names in (STATISTICS_KEY, STATISTICS_SEGMENT_BY, STATISTICS_ORDER_BY):
        assert set(names) <= set(STATISTICS_COLUMNS)
    assert STATISTICS_COLUMNS[-1] == "unit_id"
    assert set(STATISTICS_LEDGER_KEY) - {"unit_id", "instrument_id"} == {
        "session_date",
        "stat_type",
    }
