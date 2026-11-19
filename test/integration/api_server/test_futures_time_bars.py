"""The futures time-bar routes (slice 309): success, refusal and error mapping.

Two fixtures over the real 2024-12-03 ``tbbo`` day (``parity_seed``):

- **ticks only**: no provider bars, production's state when 309 shipped;
- **both**: the same day also complete in provider one-minute bars.

The reader's own behaviour is ``test_time_bar_reads``'s; this file tests the
routes' work: parameters, the source rule at the route, ``sources``,
``applied`` and the mapping of every failure.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from tick_support.parity_seed import (
    SESSION,
    ParityDb,
    seed_parity,
    seed_ticks_only,
    tick_minutes,
)
from tick_support.roll_seed import seed_tier_days
from tick_support.rows import insert_bar, insert_bar_ledger_row

from manta_trading.api_server.deps import get_max_time_bar_trades
from manta_trading.data.tick import time_bar_reads
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.schema_families import FAMILY_LEDGER, SchemaFamily
from manta_trading.data.tick.tick_format import ts_text
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


def test_from_until_alone_plan_only_the_sessions_they_reach(
    ticks_only: ParityDb, tick_client: TestClient
) -> None:
    """Issue #81: held data spans 12-02 and 12-03; a window inside 12-03's
    session plans 12-03 alone, so nothing about 12-02 is reported."""
    _seed_bars_only_session(ticks_only)
    lower = datetime.fromtimestamp(min(ticks_only.tick_minutes) / NS_PER_SECOND, UTC)

    body = _get(
        tick_client,
        PRODUCT,
        granularity="5m",
        rule="c.0",
        **{
            "from": lower.isoformat(),
            "until": (lower + timedelta(hours=1)).isoformat(),
        },
    )

    assert (body["applied"]["start"], body["applied"]["end"]) == (
        str(SESSION),
        str(SESSION + timedelta(days=1)),
    )
    assert {run["first_session"] for run in body["sources"]} == {str(SESSION)}


# -- 1-second bars (issue #40) ---------------------------------------------------


def test_one_second_bars_inside_from_until(
    ticks_only: ParityDb, tick_client: TestClient
) -> None:
    from_ns = min(ticks_only.tick_minutes) + 60 * NS_PER_SECOND
    until_ns = from_ns + 300 * NS_PER_SECOND
    lower = datetime.fromtimestamp(from_ns / NS_PER_SECOND, UTC)
    upper = datetime.fromtimestamp(until_ns / NS_PER_SECOND, UTC)

    body = _get(
        tick_client,
        PRODUCT,
        granularity="1s",
        **ONE_SESSION,
        **{"from": lower.isoformat(), "until": upper.isoformat()},
    )

    seconds = tick_minutes(ticks_only.url, ticks_only.window, NS_PER_SECOND)
    expected = {b: m for b, m in seconds.items() if from_ns <= b < until_ns}
    assert expected
    assert [b["time"] for b in body["bars"]] == [ts_text(b) for b in sorted(expected)]
    assert [b["volume"] for b in body["bars"]] == [
        expected[b][4] for b in sorted(expected)
    ]
    assert _sources(body) == {"ticks"}
    assert (body["applied"]["from"], body["applied"]["until"]) == (
        lower.isoformat().replace("+00:00", "Z"),
        upper.isoformat().replace("+00:00", "Z"),
    )


# -- error mapping: each reads through the tick cut, so 15s on ticks only ----------


def _fifteen(client: TestClient) -> Any:
    return client.get(PRODUCT, params={"granularity": "15s", **ONE_SESSION})


def test_over_the_trade_limit_is_422_with_the_count(
    ticks_only: ParityDb, tick_client: TestClient
) -> None:
    tick_client.app.dependency_overrides[get_max_time_bar_trades] = lambda: 10  # type: ignore[attr-defined]

    response = _fifteen(tick_client)

    assert response.status_code == 422
    assert "trades to cut 15s bars, over the 10 trade limit" in response.text


def test_a_disconnect_is_499_with_no_body(
    ticks_only: ParityDb, tick_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def gone(self: Request) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)

    response = _fifteen(tick_client)

    assert response.status_code == 499 and response.content == b""


def test_a_cancelled_statement_is_504(
    ticks_only: ParityDb, tick_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cancelled(*args: Any, **kwargs: Any) -> int:
        raise psycopg.errors.QueryCanceled(
            "canceling statement due to statement timeout"
        )

    monkeypatch.setattr(time_bar_reads, "cut_time_bars", cancelled)

    assert _fifteen(tick_client).status_code == 504


def test_more_trades_than_the_ledger_is_a_logged_500(
    ticks_only: ParityDb, tick_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    ledger = FAMILY_LEDGER[SchemaFamily.TICK_TIER]
    with psycopg.connect(ticks_only.url, autocommit=True) as conn:
        conn.execute(
            f"UPDATE {ledger} SET record_count = record_count - 1 WHERE ctid ="
            f" (SELECT ctid FROM {ledger} WHERE instrument_id = %s"
            " AND session_date = %s AND record_count > 0 LIMIT 1)",
            (ticks_only.window.instrument_id, SESSION),
        )

    with caplog.at_level(logging.ERROR):
        response = _fifteen(tick_client)

    assert response.status_code == 500
    assert str(SESSION) in response.text and "see the server log" in response.text
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(str(SESSION) in r.getMessage() for r in errors)
