"""The futures tick-bar routes (slice 308 D1, D4, D5, D10; FR2–FR6).

Seeded by ``tick_bar_seed`` over the September roll: ESU4 in 2024-09-17, ESZ4
in 2024-09-18 (repeated keys, every side, quotes on half) and 2024-09-19, the
ledger matching. Fixtures and error bodies are those of ``test_futures_series``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from tick_support.roll_seed import ESU4, ESZ4, SeededRoll
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit
from tick_support.tick_bar_seed import (
    FIRST_COUNT,
    ROLL_COUNT,
    SECOND,
    SECOND_COUNT,
    seed_tick_bar_sessions,
    session_start_ns,
)
from tick_support.ticks import Tick, seed_ticks

from manta_trading.api_server.deps import get_max_bars, get_max_tick_bar_trades
from manta_trading.api_server.routes import futures_tick_bars
from manta_trading.data.tick import tick_reads
from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.dbn_values import UNDEF_INT64
from manta_trading.data.tick.tick_format import ts_text

CONTRACT = "/api/v1/futures/contracts/{}/tick-bars"
PRODUCT = "/api/v1/futures/products/{}/tick-bars"
TWO_SESSIONS = {"start": "2024-09-18", "end": "2024-09-20"}
ONE_SESSION = {"start": "2024-09-18", "end": "2024-09-19"}
ROLL = {"start": "2024-09-17", "end": "2024-09-20"}
FIELDS = [
    "session_date", "index", "open_time", "close_time", "raw_symbol",
    "instrument_id", "open", "high", "low", "close", "volume", "trade_count",
    "buy_volume", "sell_volume", "bid_px", "ask_px", "partial",
]  # fmt: skip
SEC = 1_000_000_000


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    return seed_tick_bar_sessions(migrated_tick_db)


@pytest.fixture
def client(seeded: SeededRoll, tick_client: TestClient) -> TestClient:
    return tick_client


def _bars(client: TestClient, url: str, **params: Any) -> dict[str, Any]:
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# -- contract route ------------------------------------------------------------


def test_contract_bars_carry_every_field_in_order(client: TestClient) -> None:
    body = _bars(client, CONTRACT.format("ESZ4"), ticks_per_bar=7, **TWO_SESSIONS)

    bars = body["bars"]
    assert body["count"] == len(bars) > 0 and body["ticks_per_bar"] == 7
    assert all(list(bar) == FIELDS for bar in bars)
    identity = [(b["session_date"], b["index"]) for b in bars]
    assert identity == sorted(identity) and len(set(identity)) == len(identity)
    assert all(b["trade_count"] == 7 for b in bars if not b["partial"])
    per_session = {
        session: sum(b["trade_count"] for b in bars if b["session_date"] == session)
        for session in ("2024-09-18", "2024-09-19")
    }
    assert per_session == {"2024-09-18": FIRST_COUNT, "2024-09-19": SECOND_COUNT}
    assert [b["index"] for b in bars if b["session_date"] == "2024-09-19"][0] == 0
    assert any(b["bid_px"] is not None for b in bars)


def test_n_above_a_session_is_one_partial_bar_and_equal_is_one_full(
    client: TestClient,
) -> None:
    above = _bars(
        client, CONTRACT.format("ESZ4"), ticks_per_bar=FIRST_COUNT + 1, **ONE_SESSION
    )["bars"]
    equal = _bars(
        client, CONTRACT.format("ESZ4"), ticks_per_bar=FIRST_COUNT, **ONE_SESSION
    )["bars"]

    assert [(b["index"], b["trade_count"], b["partial"]) for b in above] == [
        (0, FIRST_COUNT, True)
    ]
    assert [(b["index"], b["trade_count"], b["partial"]) for b in equal] == [
        (0, FIRST_COUNT, False)
    ]


@pytest.mark.parametrize("value", ["0", "-1", "abc", None])
def test_a_bad_or_missing_ticks_per_bar_is_422(client: TestClient, value: str) -> None:
    params = dict(ONE_SESSION) | ({} if value is None else {"ticks_per_bar": value})
    assert client.get(CONTRACT.format("ESZ4"), params=params).status_code == 422


def test_from_until_equal_the_full_bars_filtered_by_open_time(
    client: TestClient,
) -> None:
    full = _bars(client, CONTRACT.format("ESZ4"), ticks_per_bar=6, **TWO_SESSIONS)
    lower = datetime(2024, 9, 18, 14, 1, 3, tzinfo=UTC)
    upper = datetime(2024, 9, 19, 14, 0, 20, tzinfo=UTC)

    cut = _bars(
        client,
        CONTRACT.format("ESZ4"),
        ticks_per_bar=6,
        **TWO_SESSIONS,
        **{"from": lower.isoformat(), "until": upper.isoformat()},
    )

    lo, hi = ts_text(_ns(lower)), ts_text(_ns(upper))
    assert cut["bars"] == [b for b in full["bars"] if lo <= b["open_time"] < hi]
    assert cut["from"] == "2024-09-18T14:01:03Z"


def _ns(instant: datetime) -> int:
    return int(instant.timestamp()) * SEC


@pytest.mark.parametrize(
    ("symbol", "params", "status"),
    [
        ("ZZZ9", ONE_SESSION, 404),
        ("ESZ4", {"start": "2024-09-19", "end": "2024-09-19"}, 422),
    ],
    ids=["unknown-contract", "empty-range"],
)
def test_contract_refusals(
    client: TestClient, symbol: str, params: dict[str, str], status: int
) -> None:
    response = client.get(
        CONTRACT.format(symbol), params={**params, "ticks_per_bar": 5}
    )
    assert response.status_code == status


def test_the_tick_database_down_is_503(closed_tick_client: TestClient) -> None:
    response = closed_tick_client.get(
        CONTRACT.format("ESZ4"), params={"ticks_per_bar": 5}
    )
    assert response.status_code == 503


# -- product route -------------------------------------------------------------


def test_product_bars_restart_at_the_roll(client: TestClient) -> None:
    body = _bars(client, PRODUCT.format("ES"), ticks_per_bar=10, **ROLL)

    by_session: dict[str, set[str]] = {}
    for bar in body["bars"]:
        by_session.setdefault(bar["session_date"], set()).add(bar["raw_symbol"])
    assert by_session == {
        "2024-09-17": {"ESU4"},
        "2024-09-18": {"ESZ4"},
        "2024-09-19": {"ESZ4"},
    }
    firsts = [b for b in body["bars"] if b["session_date"] == "2024-09-18"]
    assert firsts[0]["index"] == 0 and firsts[0]["instrument_id"] == ESZ4.instrument_id
    before = [b for b in body["bars"] if b["session_date"] == "2024-09-17"]
    assert before[0]["instrument_id"] == ESU4.instrument_id
    assert sum(b["trade_count"] for b in before) == ROLL_COUNT
    assert before[-1]["partial"] is (ROLL_COUNT % 10 != 0)


def test_product_refusals(client: TestClient) -> None:
    unknown = client.get(PRODUCT.format("ZZ"), params={"ticks_per_bar": 5})
    bad_rule = client.get(
        PRODUCT.format("ES"), params={"ticks_per_bar": 5, "rule": "bad", **ROLL}
    )

    assert unknown.status_code == 404
    assert bad_rule.status_code == 422
    assert "roll rule 'bad'" in bad_rule.json()["error"]


def test_an_incomplete_session_is_listed_and_still_served(
    client: TestClient, migrated_tick_db: str
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.TBBO.value)
        insert_unit(
            conn,
            request,
            unit_date=SECOND,
            state=UnitState.VERIFIED.value,
            **FILE_COLUMNS,
        )

    body = _bars(client, PRODUCT.format("ES"), ticks_per_bar=10, **TWO_SESSIONS)

    assert {"session_date": "2024-09-19", "status": "awaiting_ingest"} in body[
        "incomplete"
    ]
    assert any(b["session_date"] == "2024-09-19" for b in body["bars"])


# -- admission (D5) ------------------------------------------------------------


def test_over_the_trade_limit_is_422_before_any_tick_is_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[Any] = []
    original = futures_tick_bars.read_tick_bars

    async def spy(*args: Any, **kwargs: Any) -> int:
        reads.append(args[1])
        return await original(*args, **kwargs)

    monkeypatch.setattr(futures_tick_bars, "read_tick_bars", spy)
    limit = FIRST_COUNT + SECOND_COUNT - 1
    client.app.dependency_overrides[get_max_tick_bar_trades] = lambda: limit  # type: ignore[attr-defined]

    response = client.get(
        CONTRACT.format("ESZ4"), params={"ticks_per_bar": 10, **TWO_SESSIONS}
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": f"the request scans {limit + 1:,} trades, over the {limit:,}"
        " trade limit; narrow start/end"
    }
    assert reads == []
    assert (
        client.get(
            CONTRACT.format("ESZ4"), params={"ticks_per_bar": 10, **ONE_SESSION}
        ).status_code
        == 200
    )
    assert len(reads) == 1


def test_over_the_bar_ceiling_is_422_with_the_remedy(client: TestClient) -> None:
    client.app.dependency_overrides[get_max_bars] = lambda: 10  # type: ignore[attr-defined]

    response = client.get(
        CONTRACT.format("ESZ4"), params={"ticks_per_bar": 1, **ONE_SESSION}
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": "the request matches more than the 10 row limit; raise"
        " ticks_per_bar, or narrow start/end or from/until"
    }


# -- refused trades and departed clients (D1, D10) -----------------------------


def test_a_sentinel_price_is_a_logged_500(
    client: TestClient,
    seeded: SeededRoll,
    migrated_tick_db: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ts = session_start_ns(SECOND) + 3600 * SEC
    bad = Tick(ts, UNDEF_INT64, sequence=10_000)
    seed_ticks(migrated_tick_db, seeded.units[SECOND], ESZ4.instrument_id, [bad])

    with caplog.at_level(logging.ERROR, logger=futures_tick_bars.__name__):
        response = client.get(
            CONTRACT.format("ESZ4"), params={"ticks_per_bar": 5, **TWO_SESSIONS}
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "stored ticks for session 2024-09-19 of ESZ4 cannot be cut into"
        " bars; see the server log"
    }
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    text = errors[0].getMessage()
    previous = f"({session_start_ns(SECOND) + (SECOND_COUNT - 1) * SEC}, 39, 0)"
    for part in ("2024-09-19", "ESZ4", str(ESZ4.instrument_id), previous):
        assert part in text, part
    assert f"({ts}, 10000, 0)" in text


def test_a_client_gone_after_the_first_batch_is_499_unlogged(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(tick_reads, "TICK_GET_FETCH_ROWS", 10)
    asked: list[int] = []

    async def disconnected(self: Request) -> bool:
        asked.append(1)
        return len(asked) > 1

    monkeypatch.setattr(Request, "is_disconnected", disconnected)

    with caplog.at_level(logging.ERROR):
        response = client.get(
            CONTRACT.format("ESZ4"), params={"ticks_per_bar": 2, **ONE_SESSION}
        )

    assert response.status_code == 499 and response.content == b""
    assert len(asked) == 2
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
