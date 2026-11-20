"""DB-backed session admission (slice 174 task 11.2), on a throwaway database
with the full chain (NYSE seeded through 2028-12-31, migration 068)."""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
import pytest

from manta_trading.api_server.admission import Refusal, RefusalReason
from manta_trading.api_server.routes.bars_session_admission import admit_session_request
from manta_trading.constants import Granularity, SessionMode
from manta_trading.data.base.symbol_calendar import UnknownSymbolError

REG, EXT, H1 = SessionMode.REGULAR, SessionMode.EXTENDED, Granularity.H1
CEILINGS = (75_000, 195_000)


@pytest.fixture
def conn(migrated_db: str) -> Any:
    with psycopg.connect(migrated_db) as connection:
        connection.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange) VALUES "
            "('SPY.US', 'SPY', 'etf', 'US', 'NYSE', 'ETF', 'NYSE ARCA')"
        )
        yield connection


def test_horizon_end_admitted_and_past_it_refused(conn: Any) -> None:
    admit_session_request(
        conn, "SPY", (H1, REG), (date(2028, 12, 1), date(2028, 12, 31), None), CEILINGS
    )
    with pytest.raises(Refusal) as raised:
        admit_session_request(
            conn,
            "SPY",
            (H1, REG),
            (date(2028, 12, 1), date(2029, 1, 2), None),
            CEILINGS,
        )
    assert raised.value.status_code == 422
    assert raised.value.fields == {
        "reason": RefusalReason.CALENDAR_HORIZON,
        "holidays_seeded_through": "2028-12-31",
    }


def test_limit_read_checks_only_the_horizon(conn: Any) -> None:
    admit_session_request(
        conn, "SPY", (Granularity.M1, EXT), (None, None, 75_000), CEILINGS
    )
    with pytest.raises(Refusal):
        admit_session_request(
            conn, "SPY", (H1, REG), (None, date(2029, 1, 2), 10), CEILINGS
        )


def test_row_ceiling_counts_bars_per_phase(conn: Any) -> None:
    # One full week of regular 1h = 5 sessions x 7 bars = 35.
    week = (date(2026, 1, 12), date(2026, 1, 16), None)
    admit_session_request(conn, "SPY", (H1, REG), week, (35, 195_000))
    with pytest.raises(Refusal) as raised:
        admit_session_request(conn, "SPY", (H1, REG), week, (34, 195_000))
    assert raised.value.fields == {
        "reason": RefusalReason.ROW_CEILING,
        "limit": 34,
        "estimate": 35,
        "granularity": "1h",
    }


def test_minute_ceiling(conn: Any) -> None:
    week = (date(2026, 1, 12), date(2026, 1, 16), None)  # 5 x 960 extended minutes
    admit_session_request(conn, "SPY", (H1, EXT), week, (75_000, 4_800))
    with pytest.raises(Refusal) as raised:
        admit_session_request(conn, "SPY", (H1, EXT), week, (75_000, 4_799))
    assert raised.value.fields == {
        "reason": RefusalReason.MINUTE_CEILING,
        "limit": 4_799,
        "estimate": 4_800,
    }


def test_unknown_symbol(conn: Any) -> None:
    with pytest.raises(UnknownSymbolError):
        admit_session_request(
            conn,
            "NOPE",
            (H1, REG),
            (date(2026, 1, 12), date(2026, 1, 16), None),
            CEILINGS,
        )
