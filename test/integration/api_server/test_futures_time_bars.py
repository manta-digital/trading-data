"""The futures time-bar routes (slice 309): success, refusal and error mapping.

Two fixtures over the real 2024-12-03 ``tbbo`` day (``parity_seed``):

- **ticks only**: no provider bars, production's state when 309 shipped;
- **both**: the same day also complete in provider one-minute bars.

The reader's own behaviour is ``test_time_bar_reads``'s; this file tests the
routes' work: parameters, the source rule at the route, ``sources``,
``applied`` and the mapping of every failure.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.parity_seed import (
    SESSION,
    ParityDb,
    seed_parity,
    seed_ticks_only,
)
from tick_support.roll_seed import seed_tier_days
from tick_support.rows import insert_bar, insert_bar_ledger_row

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.time_bar_size import BAR_SIZE_FORM, NS_PER_SECOND

CONTRACT = "/api/v1/futures/contracts/{}/bars"
PRODUCT = "/api/v1/futures/products/ES/bars"
ONE_SESSION = {"start": "2024-12-03", "end": "2024-12-04", "rule": "c.0"}
BARS = TickSchema.OHLCV_1M.value
#: A session with neither ticks nor bars held (past the fixture's days).
EMPTY = date(2024, 12, 6)
#: The session before the tick day: given provider bars only.
BARS_ONLY = date(2024, 12, 2)


@pytest.fixture
async def ticks_only(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> ParityDb:
    return await seed_ticks_only(migrated_tick_db, session_migrated_db, tmp_path)


@pytest.fixture
async def both(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> ParityDb:
    return await seed_parity(migrated_tick_db, session_migrated_db, tmp_path)


def _get(client: TestClient, url: str, **params: Any) -> dict[str, Any]:
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _sources(body: dict[str, Any]) -> set[str]:
    return {run["source"] for run in body["sources"]}


# -- success: ticks only (criterion 1 at test scale) ----------------------------


@pytest.mark.parametrize("size", ["1h", "1m", "15s", "1d"])
def test_ticks_only_product_bars_come_from_ticks(
    ticks_only: ParityDb, tick_client: TestClient, size: str
) -> None:
    body = _get(tick_client, PRODUCT, granularity=size, **ONE_SESSION)

    assert body["count"] == len(body["bars"]) > 0
    assert body["sources"] == [
        {"first_session": "2024-12-03", "last_session": "2024-12-03", "source": "ticks"}
    ]
    assert body["applied"] == {
        "granularity": size,
        "rule": "c.0",
        "start": "2024-12-03",
        "end": "2024-12-04",
        "from": None,
        "until": None,
        "format": "json",
    }


def test_ticks_only_contract_bars_come_from_ticks(
    ticks_only: ParityDb, tick_client: TestClient
) -> None:
    params = {"start": "2024-12-03", "end": "2024-12-04", "granularity": "1h"}
    body = _get(tick_client, CONTRACT.format(ticks_only.symbol), **params)

    assert body["count"] > 0 and _sources(body) == {"ticks"}
    assert "rule" not in body["applied"]


# -- success: both (criterion 3 at the route) ----------------------------------


@pytest.mark.parametrize(
    ("size", "source"), [("5m", BARS), ("1d", BARS), ("15s", "ticks")]
)
def test_held_bars_serve_what_they_can_make(
    both: ParityDb, tick_client: TestClient, size: str, source: str
) -> None:
    body = _get(tick_client, PRODUCT, granularity=size, **ONE_SESSION)

    assert body["count"] > 0
    assert _sources(body) == {source}


# -- refusals --------------------------------------------------------------------


@pytest.mark.parametrize("size", ["0s", "2d", "5x"])
def test_a_size_outside_the_grammar_is_422(
    ticks_only: ParityDb, tick_client: TestClient, size: str
) -> None:
    response = tick_client.get(PRODUCT, params={"granularity": size, **ONE_SESSION})

    assert response.status_code == 422
    assert BAR_SIZE_FORM in response.text


def test_a_session_with_neither_ticks_nor_bars_is_incomplete(
    ticks_only: ParityDb, tick_client: TestClient
) -> None:
    """Criterion 4."""
    params = {"start": str(EMPTY), "end": str(EMPTY + timedelta(days=1))}
    body = _get(tick_client, PRODUCT, granularity="1h", rule="c.0", **params)

    assert body["bars"] == [] and body["sources"] == []
    assert [i["session_date"] for i in body["incomplete"]] == [str(EMPTY)]


# -- default range ---------------------------------------------------------------


def _seed_bars_only_session(db: ParityDb) -> None:
    """Provider minutes for session 12-02 (no ticks), complete in its family:
    ingested bar units on both UTC days it touches."""
    units = seed_tier_days(
        db.url, [BARS_ONLY - timedelta(days=1), BARS_ONLY], TickSchema.OHLCV_1M
    )
    start = int(datetime(2024, 12, 2, 14, tzinfo=UTC).timestamp()) * NS_PER_SECOND
    stamps = [start + n * 60 * NS_PER_SECOND for n in range(10)]
    instrument = db.window.instrument_id
    with psycopg.connect(db.url, autocommit=True) as conn:
        for ts in stamps:
            insert_bar(conn, units[BARS_ONLY], ts_event=ts, instrument_id=instrument)
        insert_bar_ledger_row(
            conn,
            units[BARS_ONLY],
            instrument_id=instrument,
            session_date=BARS_ONLY,
            record_count=len(stamps),
            volume=7 * len(stamps),
            first_event_ns=stamps[0],
            last_event_ns=stamps[-1],
        )


def test_the_default_range_spans_held_ticks_and_bars(
    ticks_only: ParityDb, tick_client: TestClient
) -> None:
    _seed_bars_only_session(ticks_only)

    body = _get(tick_client, PRODUCT, granularity="5m", rule="c.0")

    assert body["applied"]["start"] == str(BARS_ONLY)
    assert body["applied"]["end"] > str(SESSION)
    by_source = {run["source"]: run for run in body["sources"]}
    assert by_source[BARS]["first_session"] == str(BARS_ONLY)
    assert by_source["ticks"]["first_session"] == str(SESSION)
    assert any(b["session_date"] == str(BARS_ONLY) for b in body["bars"])
