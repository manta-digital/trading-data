"""The futures time-series read path and routes (slice 231, FR1–FR6; TD3–TD6).

The September 2024 roll is seeded (``roll_seed``) plus ``N_TICKS`` ESZ4 ticks,
one a minute from 14:00 UTC in session 2024-09-18 (22:00 UTC 09-17 → 21:00 UTC
09-18), with the ledger's record count set to match. CLI comparisons run
``mt data tick get --json`` in-process over the same databases.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import msgpack
import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.cli import clear_tick_env, cli_settings, closed_port
from tick_support.roll_seed import ESU4, ESZ4, SeededRoll, seed_september_roll
from tick_support.rows import (
    FILE_COLUMNS,
    insert_definition,
    insert_request,
    insert_unit,
)
from tick_support.ticks import Tick, seed_ticks
from typer.testing import CliRunner

from manta_trading.api_server.deps import get_max_bars
from manta_trading.api_server.futures_read import RowCeilingExceeded, read_series
from manta_trading.cli.app import app as cli_app
from manta_trading.data.tick import tick_reads
from manta_trading.data.tick.constants import (
    OUTRIGHT_CLASS,
    TICK_ROLL_MAX_SESSIONS,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.get_plan import GetPlan, contract_plan
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import OnBatch, Row, TickWindow, read_ticks

from .conftest import SECOND, client_for

N_TICKS = 30
SESSION = date(2024, 9, 18)
SESSION_RANGE = {"start": "2024-09-18", "end": "2024-09-19"}
ROLL_RANGE = {"start": "2024-09-16", "end": "2024-09-20"}
CONTRACT = "/api/v1/futures/contracts/{}/{}"
PRODUCT = "/api/v1/futures/products/{}/{}"


def _at(minute: int) -> datetime:
    return datetime(2024, 9, 18, 14, minute, tzinfo=UTC)


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    roll = seed_september_roll(migrated_tick_db)
    ticks = [
        Tick(utc_ns(_at(n)), price=5_650_250_000_000 + n * 250_000_000, sequence=n)
        for n in range(N_TICKS)
    ]
    unit = roll.units[SESSION]
    seed_ticks(migrated_tick_db, unit, ESZ4.instrument_id, ticks)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_ingest_ledger SET record_count = %s"
            " WHERE unit_id = %s AND instrument_id = %s AND session_date = %s",
            (N_TICKS, unit, ESZ4.instrument_id, SESSION),
        )
    return roll


@pytest.fixture
def client(seeded: SeededRoll, tick_client: TestClient) -> TestClient:
    return tick_client


def _cli_get(monkeypatch: pytest.MonkeyPatch, urls: tuple[str, str], *args: str) -> Any:
    clear_tick_env(monkeypatch)
    with cli_settings(*urls):
        result = CliRunner().invoke(cli_app, ["data", "tick", "get", *args, "--json"])
    assert result.exit_code in (0, 3), result.output
    return json.loads(result.stdout)


# -- the read helper (6.3) -----------------------------------------------------


async def _helper(
    tick_url: str,
    calendar_url: str,
    ceiling: int,
    bounds: tuple[int | None, int | None] = (None, None),
    read: Any = read_ticks,
) -> tuple[GetPlan, list[Row]]:
    async with await psycopg.AsyncConnection.connect(tick_url, autocommit=True) as conn:

        async def plan_for(c: Conn) -> GetPlan:
            return await contract_plan(
                c, calendar_url, "ESZ4", SESSION, date(2024, 9, 19)
            )

        return await read_series(conn, plan_for, read, ceiling, bounds)


async def test_the_helper_stops_at_the_ceiling_plus_one_batch(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = 4
    monkeypatch.setattr(tick_reads, "TICK_GET_FETCH_ROWS", batch)
    seen: list[int] = []

    async def spy(conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
        def counting(rows: Sequence[Row]) -> None:
            seen.append(len(rows))
            on_batch(rows)

        return await read_ticks(conn, window, counting)

    with pytest.raises(RowCeilingExceeded):
        await _helper(migrated_tick_db, session_migrated_db, 10, read=spy)

    assert 10 < sum(seen) <= 10 + batch


async def test_exactly_the_ceiling_succeeds(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    bounds = (utc_ns(_at(0)), utc_ns(_at(10)))

    _plan, rows = await _helper(migrated_tick_db, session_migrated_db, 10, bounds)

    assert len(rows) == 10


async def test_consecutive_bounds_page_the_session_exactly(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    """FR6: [open, a), [a, b), [b, close] return the ledger's count, once each."""
    edges = [None, utc_ns(_at(7)), utc_ns(_at(19)), None]
    pages = [
        (await _helper(migrated_tick_db, session_migrated_db, 100, (lo, hi)))[1]
        for lo, hi in zip(edges, edges[1:], strict=False)
    ]
    keys = [(row[0], row[13]) for page in pages for row in page]
    with psycopg.connect(migrated_tick_db) as conn:
        (ledger,) = conn.execute(
            "SELECT sum(record_count) FROM tick_ingest_ledger"
            " WHERE instrument_id = %s AND session_date = %s",
            (ESZ4.instrument_id, SESSION),
        ).fetchone()  # type: ignore[misc]

    assert [len(p) for p in pages] == [7, 12, 11]
    assert len(keys) == len(set(keys)) == ledger


# -- contract ticks (6.5) ------------------------------------------------------


def test_contract_ticks_equal_the_cli(
    client: TestClient,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR1: field for field, in order."""
    body = client.get(CONTRACT.format("ESZ4", "ticks"), params=SESSION_RANGE).json()
    cli = _cli_get(
        monkeypatch,
        (migrated_tick_db, session_migrated_db),
        "ticks",
        *("--contract", "ESZ4", "--start", "2024-09-18", "--end", "2024-09-19"),
    )

    assert body["count"] == N_TICKS == cli["row_count"]
    assert body["ticks"] == cli["rows"]
    assert body["target"] == {
        "kind": "contract",
        "product": None,
        "contract": "ESZ4",
        "instrument_id": ESZ4.instrument_id,
        "rule": None,
        "is_default": None,
    }
    assert body["unresolved"] == []


def test_msgpack_decodes_to_the_same_body(client: TestClient) -> None:
    path = CONTRACT.format("ESZ4", "ticks")
    as_json = client.get(path, params=SESSION_RANGE).json()
    packed = client.get(path, params={**SESSION_RANGE, "format": "msgpack"})

    assert packed.headers["content-type"] == "application/x-msgpack"
    assert msgpack.unpackb(packed.content) == as_json


def test_naive_bounds_read_as_utc(client: TestClient) -> None:
    path = CONTRACT.format("ESZ4", "ticks")
    naive = {
        **SESSION_RANGE,
        "from": "2024-09-18T14:05:00",
        "until": "2024-09-18T14:09:00",
    }
    aware = {
        **SESSION_RANGE,
        "from": "2024-09-18T14:05:00Z",
        "until": "2024-09-18T14:09:00Z",
    }

    got = client.get(path, params=naive).json()

    assert got == client.get(path, params=aware).json()
    assert got["count"] == 4 and got["from"] == "2024-09-18T14:05:00Z"


@pytest.mark.parametrize(
    ("params", "status", "message"),
    [
        ({}, 404, "no definition held for contract ZZZ9"),
        ({"start": "2024-09-18", "end": "2024-09-18"}, 422, "is empty"),
        (
            {"from": "2024-09-18T14:05:00Z", "until": "2024-09-18T14:05:00Z"},
            422,
            "must be before until",
        ),
        (
            {"from": "2024-09-18T14:06:00Z", "until": "2024-09-18T14:05:00Z"},
            422,
            "must be before until",
        ),
    ],
    ids=["unknown", "empty-range", "from-equals-until", "from-after-until"],
)
def test_contract_ticks_refusals(
    client: TestClient, params: dict[str, str], status: int, message: str
) -> None:
    symbol = "ZZZ9" if status == 404 else "ESZ4"
    response = client.get(CONTRACT.format(symbol, "ticks"), params=params)

    assert response.status_code == status
    assert message in response.json()["error"]


def test_an_ambiguous_symbol_is_422_naming_both(
    seeded: SeededRoll, migrated_tick_db: str, client: TestClient
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        insert_definition(
            conn,
            seeded.definition_unit,
            instrument_id=777,
            raw_symbol="ESZ4",
            activation_ns=utc_ns(datetime(2013, 6, 1, tzinfo=UTC)),
            expiration_ns=utc_ns(datetime(2014, 12, 19, 14, 30, tzinfo=UTC)),
            instrument_class=OUTRIGHT_CLASS,
        )

    response = client.get(CONTRACT.format("ESZ4", "ticks"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert "777" in error and str(ESZ4.instrument_id) in error


def test_the_ceiling_refuses_naming_the_limit_and_remedy(client: TestClient) -> None:
    """FR5, with the ceiling overridden to 10."""
    client.app.dependency_overrides[get_max_bars] = lambda: 10  # type: ignore[attr-defined]

    response = client.get(CONTRACT.format("ESZ4", "ticks"), params=SESSION_RANGE)

    assert response.status_code == 422
    assert response.json() == {
        "error": "the request matches more than the 10 row limit; "
        "narrow start/end or from/until"
    }


@pytest.mark.parametrize("what", ["ticks", "bars?granularity=1m"])
def test_contract_routes_are_503_with_the_tick_database_down(
    closed_tick_client: TestClient, what: str
) -> None:
    assert closed_tick_client.get(CONTRACT.format("ESZ4", what)).status_code == 503


# -- contract bars (6.7) -------------------------------------------------------


@pytest.mark.parametrize("granularity", ["1m", "1d"])
def test_contract_bars_equal_the_cli(
    client: TestClient,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
    granularity: str,
) -> None:
    body = client.get(
        CONTRACT.format("ESZ4", "bars"),
        params={**SESSION_RANGE, "granularity": granularity},
    ).json()
    cli = _cli_get(
        monkeypatch,
        (migrated_tick_db, session_migrated_db),
        granularity,
        *("--contract", "ESZ4", "--start", "2024-09-18", "--end", "2024-09-19"),
    )

    assert body["bars"] == cli["rows"] and body["count"] > 0
    assert body["granularity"] == granularity


@pytest.mark.parametrize("granularity", ["2h", "1w", "1mo"])
def test_a_bar_size_outside_the_tick_sizes_is_422(
    client: TestClient, granularity: str
) -> None:
    response = client.get(
        CONTRACT.format("ESZ4", "bars"), params={"granularity": granularity}
    )

    assert response.status_code == 422


# -- product routes (6.9) ------------------------------------------------------


def test_product_bars_across_the_roll_equal_the_cli(
    client: TestClient,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR2."""
    body = client.get(
        PRODUCT.format("ES", "bars"), params={**ROLL_RANGE, "granularity": "1d"}
    ).json()
    cli = _cli_get(
        monkeypatch,
        (migrated_tick_db, session_migrated_db),
        "1d",
        *(
            "--product",
            "ES",
            "--start",
            ROLL_RANGE["start"],
            "--end",
            ROLL_RANGE["end"],
        ),
    )

    assert [s["raw_symbol"] for s in body["segments"]] == [ESU4.raw_symbol, "ESZ4"]
    assert body["segments"] == cli["segments"]
    assert (body["target"]["rule"], body["target"]["is_default"]) == ("v.0", True)
    assert body["bars"] == cli["rows"]


def test_the_first_held_sessions_are_unresolved_with_http_200(
    client: TestClient,
) -> None:
    """FR3: the volume rule has no lag session before the first one held."""
    response = client.get(
        PRODUCT.format("ES", "bars"),
        params={"start": "2024-09-03", "end": "2024-09-06", "granularity": "1d"},
    )

    assert response.status_code == 200
    unresolved = [u["session_date"] for u in response.json()["unresolved"]]
    assert unresolved and unresolved[0] == "2024-09-03"


def test_an_incomplete_session_is_listed(
    client: TestClient, migrated_tick_db: str
) -> None:
    """FR3: a verified-not-ingested unit for 09-19 leaves sessions awaiting."""
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.TBBO.value)
        insert_unit(
            conn,
            request,
            unit_date=date(2024, 9, 19),
            state=UnitState.VERIFIED.value,
            **FILE_COLUMNS,
        )

    body = client.get(
        PRODUCT.format("ES", "ticks"),
        params={"start": "2024-09-18", "end": "2024-09-20"},
    ).json()

    assert {"session_date": "2024-09-19", "status": "awaiting_ingest"} in body[
        "incomplete"
    ]


def test_the_calendar_rule_changes_the_segments(client: TestClient) -> None:
    def segments(rule: str) -> list[tuple[str, str]]:
        body = client.get(
            PRODUCT.format("ES", "bars"),
            params={
                "start": "2024-09-16",
                "end": "2024-09-25",
                "granularity": "1d",
                "rule": rule,
            },
        ).json()
        return [(s["raw_symbol"], s["first_session"]) for s in body["segments"]]

    assert segments("c.0") != segments("v.0")


@pytest.mark.parametrize(
    ("params", "status", "message"),
    [
        ({"rule": "bad"}, 422, "roll rule 'bad'"),
        (
            {"start": "2020-01-02", "end": "2026-01-02"},
            422,
            f"TICK_ROLL_MAX_SESSIONS ({TICK_ROLL_MAX_SESSIONS})",
        ),
    ],
    ids=["bad-rule", "over-cap"],
)
def test_product_refusals(
    client: TestClient, params: dict[str, str], status: int, message: str
) -> None:
    response = client.get(
        PRODUCT.format("ES", "bars"), params={**params, "granularity": "1d"}
    )

    assert response.status_code == status
    assert message in response.json()["error"]


def test_an_unknown_product_is_404(client: TestClient) -> None:
    response = client.get(PRODUCT.format("ZZ", "ticks"))

    assert (response.status_code, response.json()) == (
        404,
        {"error": "Product 'ZZ' not found"},
    )


def test_a_calendar_outage_in_the_roll_path_is_503(
    seeded: SeededRoll, session_migrated_db: str, migrated_tick_db: str
) -> None:
    for client in client_for(session_migrated_db, migrated_tick_db):
        client.app.state.calendar_url = (  # type: ignore[attr-defined]
            f"postgresql://x@127.0.0.1:{closed_port()}/x"
        )
        response = client.get(
            PRODUCT.format("ES", "bars"), params={**ROLL_RANGE, "granularity": "1d"}
        )

    assert response.status_code == 503


def test_a_second_product_serves_bars(
    two_products: None, tick_client: TestClient
) -> None:
    response = tick_client.get(
        PRODUCT.format(SECOND.product, "bars"), params={"granularity": "1d"}
    )

    assert response.status_code == 200
    assert response.json()["bars"] == []
