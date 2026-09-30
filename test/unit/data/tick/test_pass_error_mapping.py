"""Bad input ends a unit or the pass with a mapped error, never a raw one (224).

A raw ``TypeError``/``ValueError``/``KeyError`` escapes ``run_phase`` as a
traceback: no report, no exit-code mapping, and a unit left where every later
run hits it again.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from manta_trading.data.tick.constants import SType
from manta_trading.data.tick.definitions import DefinitionRejected, _value
from manta_trading.data.tick.tick_calendar import (
    TickCalendarError,
    planning_product,
    product_session_days,
)


@pytest.mark.parametrize("raw", [1.5, None, "text"])
def test_a_malformed_definition_field_rejects_the_unit(raw: object) -> None:
    with pytest.raises(DefinitionRejected, match="expiration"):
        _value("expiration", raw)


def test_numpy_integers_and_bytes_decode() -> None:
    assert _value("instrument_id", np.uint32(42)) == 42
    assert _value("raw_symbol", np.bytes_(b"ESZ4")) == "ESZ4"


def test_a_shape_with_no_product_is_a_calendar_error() -> None:
    with pytest.raises(TickCalendarError, match="no calendar"):
        planning_product(SType.RAW_SYMBOL, ("ESZ4",))


def test_a_product_with_no_calendar_is_a_calendar_error() -> None:
    # Raised before any connection is opened, so the URL is never used.
    with pytest.raises(TickCalendarError, match="ZZ"):
        product_session_days("unused", "ZZ", date(2024, 1, 2), date(2024, 1, 3))
