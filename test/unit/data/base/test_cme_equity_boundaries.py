"""Real ES trade boundaries land in their seeded CME_EQUITY sessions (221 8.3).

The fixture holds the first and last ``ts_event`` of the 2024 holiday sessions
in the two adopted Databento jobs (see its ``provenance``). Sessions come from
``populate_trading_sessions`` over ``CME_EQUITY_EXCEPTIONS`` — no database.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from manta_trading.data.base.session_index import NO_SESSION, Session, SessionIndex
from manta_trading.data.base.session_population import populate_trading_sessions
from manta_trading.market.schema.seed_cme_calendar import (
    CME_EQUITY_CALENDAR,
    CME_EQUITY_CALENDAR_ID,
    CME_EQUITY_EXCEPTIONS,
)

_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "test"
    / "fixtures"
    / "calendar"
    / "cme_equity_2024_boundaries.json"
)
_BOUNDARIES: list[dict[str, Any]] = json.loads(_FIXTURE.read_text())["sessions"]
_NS_PER_SECOND = 1_000_000_000
_LAST_TRADE_WINDOW_NS = 60 * _NS_PER_SECOND


@pytest.fixture(scope="module")
def index() -> SessionIndex:
    rows = populate_trading_sessions(
        CME_EQUITY_CALENDAR_ID,
        date(2024, 8, 1),
        date(2024, 12, 31),
        CME_EQUITY_CALENDAR,
        [
            {**row, "market_status": row["market_status"].value}
            for row in CME_EQUITY_EXCEPTIONS
        ],
    )
    return SessionIndex(
        [
            Session(
                r["calendar_id"],
                r["session_date"],
                r["session_open_utc"],
                r["session_close_utc"],
            )
            for r in rows
        ]
    )


@pytest.mark.parametrize("boundary", _BOUNDARIES, ids=lambda b: b["label"])
def test_first_and_last_trade_land_in_their_session(
    index: SessionIndex, boundary: dict[str, Any]
) -> None:
    stamps = np.array(
        [boundary["first_ts_event_ns"], boundary["last_ts_event_ns"]], dtype=np.int64
    )
    positions = index.locate_ns(stamps)
    assert NO_SESSION not in positions.tolist()
    dates = {index.sessions[p].session_date.isoformat() for p in positions.tolist()}
    assert dates == {boundary["session_date"]}


@pytest.mark.parametrize("boundary", _BOUNDARIES, ids=lambda b: b["label"])
def test_last_trade_is_just_before_the_seeded_close(
    index: SessionIndex, boundary: dict[str, Any]
) -> None:
    """ES trades up to its halt, so a wrong early-close time shows up here."""
    session = index.sessions[
        int(index.locate_ns(np.array([boundary["last_ts_event_ns"]]))[0])
    ]
    close_ns = int(session.close_utc.timestamp()) * _NS_PER_SECOND
    assert 0 <= close_ns - boundary["last_ts_event_ns"] < _LAST_TRADE_WINDOW_NS


def test_fixture_covers_the_early_closes() -> None:
    labels = {b["label"] for b in _BOUNDARIES}
    assert {"Labor Day", "Thanksgiving", "Black Friday", "Christmas Eve"} <= labels
