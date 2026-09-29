"""Tick-track rendering helpers and id discipline (slice 222 TD8, TD9)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import timedelta
from enum import StrEnum

import pytest

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    ARCHIVED_SCHEMAS,
    TICK_TRADE_CHUNK_INTERVAL,
    UNIT_STATES_WITH_FILE,
    DeliveryMode,
    SType,
    UnitState,
)
from manta_trading.market.schema.migrations.tick import (
    TICK_MIGRATIONS,
    _check_in,
    _in_list,
    _interval_ns,
)
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

_QUOTED = re.compile(r"'([^']*)'")

#: Every CHECK source in TD9's table.
_SOURCES: dict[str, Iterable[StrEnum]] = {
    "ARCHIVED_SCHEMAS": ARCHIVED_SCHEMAS,
    "SType": SType,
    "DeliveryMode": DeliveryMode,
    "UnitState": UnitState,
    "FetchStatus": FetchStatus,
    "UNIT_STATES_WITH_FILE": UNIT_STATES_WITH_FILE,
}


@pytest.mark.parametrize("source", _SOURCES.values(), ids=_SOURCES.keys())
def test_rendered_list_equals_the_enum(source: Iterable[StrEnum]) -> None:
    rendered = _in_list(source)
    values = _QUOTED.findall(rendered)
    assert set(values) == {m.value for m in source}
    assert values == sorted(values)


def test_check_in_names_the_column() -> None:
    assert _check_in("state", UnitState) == f"CHECK (state IN ({_in_list(UnitState)}))"


def test_chunk_interval_renders_to_exact_nanoseconds() -> None:
    assert _interval_ns(TICK_TRADE_CHUNK_INTERVAL) == 604_800_000_000_000


def test_interval_ns_keeps_sub_second_precision() -> None:
    assert _interval_ns(timedelta(seconds=1, microseconds=1)) == 1_000_001_000


def test_track_starts_with_the_bootstrap() -> None:
    assert TICK_MIGRATIONS[0]["id"] == BOOTSTRAP_MIGRATION_ID


def test_later_ids_are_prefixed_unique_and_ascending() -> None:
    ids = [m["id"] for m in TICK_MIGRATIONS[1:]]
    assert all(re.match(r"tick_\d{3}_", i) for i in ids)
    assert ids == sorted(set(ids))
