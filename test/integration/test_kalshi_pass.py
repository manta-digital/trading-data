"""Integration tests: ``mt data kalshi pass`` end to end (slice 263, Task 7.1).

Fake source, real repository, real connection preflight and advisory lock —
only ``ephemeral_db`` (``MT_TIMESCALE_TEST_URL``), never the production URL.
The pass is proven interchangeable with 262's ``sync``: same fixtures, same
final state.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from kalshi_helpers import apply_kalshi_track, column
from kalshi_support.fake_candle_source import make_candle
from kalshi_support.fake_source import make_market
from psycopg import sql
from test_kalshi_sync import EVENT, _live, _settings, _settled, _source

from manta_trading.cli.commands import kalshi as cmd
from manta_trading.data.kalshi.candle_plan import last_complete_period
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    COLLECTED_CANDLE_PERIOD,
    SYNC_ADVISORY_LOCK_KEY,
    MarketStatus,
    MarketStatusFilter,
    Surface,
)
from manta_trading.data.kalshi.db import LOCK_HELD, open_sync_connection

#: The tables the two commands must leave identical (Criterion 1).
_CATALOG_TABLES = ("series", "events", "markets")


async def run_pass_cli(
    kalshi_db: str,
    source: object,
    capsys: pytest.CaptureFixture[str],
    *,
    events_file: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """``run_pass`` exactly as the command calls it, with the client swapped."""
    with patch.object(KalshiClient, "from_settings", return_value=source):
        code = await cmd.run_pass(_settings(kalshi_db), events_file, True)
    return code, json.loads(capsys.readouterr().out)


async def _counts(conn: psycopg.AsyncConnection[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in _CATALOG_TABLES:
        query = sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier("kalshi", table)
        )
        cursor = await conn.execute(query)
        row = await cursor.fetchone()
        assert row is not None
        out[table] = row[0]
    return out


async def _sync_state(
    conn: psycopg.AsyncConnection[Any], surface: Surface | None = None
) -> list[Any]:
    """``(surface, has last_full_sync, has watermark)`` per row, optionally
    for one surface (the pass-equals-sync proof is about the catalog's)."""
    return await column(
        conn,
        "SELECT (surface, last_full_sync_at IS NOT NULL, watermark_ts IS NOT NULL)"
        "::text FROM kalshi.sync_state WHERE surface = COALESCE(%s, surface) "
        "ORDER BY surface",
        surface.value if surface else None,
    )


class TestPassEndToEnd:
    async def test_first_pass_populates_and_sets_state(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 1: exit 0 and the catalog surface's state row is set."""
        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"), _live("B"), _live("C"))
        source.add_settled(_settled("S-A", timedelta(minutes=30)))
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK, summary
        assert summary["exit_code"] == 0 and summary["outcome"] == "ok"
        assert summary["phases"][0]["name"] == "catalog"
        assert summary["phases"][0]["outcome"] == "ok"
        catalog = summary["phases"][0]["summary"]
        assert catalog["phases"]["markets"]["written"] == 3
        # Both phases wrote their surface's row (slice 264 adds candlesticks).
        assert await _sync_state(kalshi_conn) == [
            "(candlesticks,t,t)",
            "(catalog,t,t)",
        ]
        assert source.closed is True

    async def test_second_pass_writes_nothing(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Write-on-change holds through the pass, as it does through sync."""
        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"), _live("B"))
        assert (await run_pass_cli(kalshi_db, source, capsys))[0] == cmd.EXIT_OK
        before = await column(
            kalshi_conn, "SELECT first_seen_at FROM kalshi.markets ORDER BY ticker"
        )
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK
        catalog = summary["phases"][0]["summary"]
        assert [catalog["phases"][p]["written"] for p in _CATALOG_TABLES] == [0, 0, 0]
        after = await column(
            kalshi_conn, "SELECT first_seen_at FROM kalshi.markets ORDER BY ticker"
        )
        assert after == before

    async def test_events_file_order_and_one_run_id(
        self, kalshi_db: str, capsys, tmp_path: Path
    ):
        """Criterion 4 against the real repository."""
        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"))
        source.add_settled(_settled("S-A", timedelta(minutes=30)))
        path = tmp_path / "pass.jsonl"
        code, _ = await run_pass_cli(kalshi_db, source, capsys, events_file=path)
        assert code == cmd.EXIT_OK
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert [e["event_type"] for e in events] == [
            "pass_started",
            "run_started",
            *["phase_finished"] * 5,
            "run_finished",
            "phase_finished",  # the candle phase (slice 264)
            "pass_finished",
        ]
        assert events[-2]["phase"] == "candles"
        assert len({e["run_id"] for e in events}) == 1


@pytest.fixture()
def second_kalshi_db(ephemeral_db_second: str) -> str:
    """A second throwaway database with the kalshi track applied.

    ``kalshi_db`` is function-scoped, so requesting it twice yields the same
    database; the pass-equals-sync proof needs two independent ones.
    """
    return apply_kalshi_track(ephemeral_db_second)


@pytest.fixture()
def ephemeral_db_second(test_admin_url: str) -> Iterator[str]:
    """A second UUID-named database, dropped on teardown.

    ``ephemeral_db``'s pattern, repeated because a fixture can only be
    requested once per test and the pass-equals-sync proof needs two
    independent databases. Like every fixture in this tier it can only ever
    name a database it just created.
    """
    db_name = f"mt_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(test_admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    parsed = urlparse(test_admin_url)
    try:
        yield urlunparse(parsed._replace(path=f"/{db_name}"))
    finally:
        with psycopg.connect(test_admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a "
                "JOIN pg_roles r ON r.rolname = a.usename "
                "WHERE a.datname = %s AND a.pid <> pg_backend_pid() "
                "AND NOT r.rolsuper",
                (db_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name))
            )


class TestPassEqualsSync:
    async def test_same_fixtures_leave_the_same_state(
        self, kalshi_db: str, second_kalshi_db: str, capsys
    ):
        """Criterion 1: a pass and a sync are interchangeable."""
        from test_kalshi_sync import run_cli

        def fixtures():
            source = _source()
            source.add_live(MarketStatusFilter.OPEN, _live("A"), _live("B"))
            source.add_settled(_settled("S-A", timedelta(minutes=30)))
            return source

        since = datetime.now(UTC) - timedelta(hours=2)
        assert (await run_cli(kalshi_db, fixtures(), capsys, settled_since=since))[
            0
        ] == cmd.EXIT_OK
        assert (await run_pass_cli(second_kalshi_db, fixtures(), capsys))[
            0
        ] == cmd.EXIT_OK

        async with (
            await psycopg.AsyncConnection.connect(kalshi_db, autocommit=True) as a,
            await psycopg.AsyncConnection.connect(
                second_kalshi_db, autocommit=True
            ) as b,
        ):
            assert await _counts(a) == await _counts(b)
            catalog = Surface.CATALOG
            assert await _sync_state(a, catalog) == await _sync_state(b, catalog)


class TestPassPreflight:
    async def test_held_lock_exits_one(self, kalshi_db: str, capsys):
        """Criterion 2, lock case: the pass refuses while a sync holds the lock."""
        holder = await open_sync_connection(kalshi_db)
        try:
            with patch.object(KalshiClient, "from_settings", return_value=_source()):
                code = await cmd.run_pass(_settings(kalshi_db), None, True)
        finally:
            await holder.close()
        assert code == cmd.EXIT_PREFLIGHT
        assert LOCK_HELD in capsys.readouterr().err

    async def test_lock_is_released_after_a_pass(self, kalshi_db: str, capsys):
        assert (await run_pass_cli(kalshi_db, _source(), capsys))[0] == cmd.EXIT_OK
        conn = await open_sync_connection(kalshi_db)
        try:
            # Scoped to *this* database: pg_locks is cluster-wide, and the
            # test cluster is shared — another session's kalshi run against
            # its own throwaway database holds the same advisory key and
            # would otherwise be counted here (observed 2026-08-27).
            held = await column(
                conn,
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND objid = %s AND database = "
                "(SELECT oid FROM pg_database WHERE datname = current_database())",
                SYNC_ADVISORY_LOCK_KEY,
            )
            assert held == [1]  # only this probe's own lock
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# Both phases end to end (slice 264, Task 7.1)
# ---------------------------------------------------------------------------


def _traded_live(ticker: str) -> Any:
    """A live market rule C selects: traded in the last 24 h, Economics."""
    return make_market(
        ticker,
        EVENT,
        status=MarketStatus.ACTIVE.value,
        close_time=datetime.now(UTC) + timedelta(days=1),
        result=None,
        settlement_ts=None,
        volume_24h_fp="5.00",
        volume_fp="50.00",
    )


def _traded_settled(ticker: str, settled_ago: timedelta) -> Any:
    ts = datetime.now(UTC) - settled_ago
    return make_market(
        ticker,
        EVENT,
        status=MarketStatus.FINALIZED.value,
        result="yes",
        close_time=ts - timedelta(minutes=1),
        settlement_ts=ts,
        volume_24h_fp="0.00",
        volume_fp="50.00",
    )


async def _candle_rows(conn: psycopg.AsyncConnection[Any]) -> list[Any]:
    return await column(
        conn,
        "SELECT (market_ticker, end_period_ts)::text FROM kalshi.candlesticks "
        "ORDER BY market_ticker, end_period_ts",
    )


async def _duplicates(conn: psycopg.AsyncConnection[Any]) -> list[Any]:
    return await column(
        conn,
        "SELECT market_ticker FROM kalshi.candlesticks "
        "GROUP BY market_ticker, period, end_period_ts HAVING count(*) > 1",
    )


async def _state(conn: psycopg.AsyncConnection[Any]) -> dict[str, datetime]:
    cursor = await conn.execute(
        "SELECT market_ticker, watermark_ts FROM kalshi.market_candle_state"
    )
    return {ticker: mark for ticker, mark in await cursor.fetchall()}


class TestTwoPhasePass:
    async def test_both_phases_candles_and_state_including_idle(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criteria 1 and 3: both phases in order; candles under the natural
        key; a state row for every requested market, idle ones included."""
        source = _source()
        source.add_live(
            MarketStatusFilter.OPEN, _traded_live("A"), _traded_live("IDLE")
        )
        last = last_complete_period(datetime.now(UTC), COLLECTED_CANDLE_PERIOD)
        source.candles.add_candles(
            "A", make_candle(last - timedelta(minutes=2)), make_candle(last)
        )
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK, summary
        assert [p["name"] for p in summary["phases"]] == ["catalog", "candles"]
        candles = summary["phases"][1]["summary"]
        assert candles["candles_written"] == 2
        assert candles["markets_requested"] == 2 and candles["markets_advanced"] == 2
        assert candles["pending"]["live"] == 2
        rows = await _candle_rows(kalshi_conn)
        assert len(rows) == 2 and all(row.startswith("(A,") for row in rows)
        state = await _state(kalshi_conn)
        assert set(state) == {"A", "IDLE"}
        assert state["IDLE"] == last  # served nothing, still advanced

    async def test_second_pass_writes_only_what_is_new(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 4: re-fetching an overlapping window writes only the new
        candle; no duplicate row exists."""
        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _traded_live("A"))
        last = last_complete_period(datetime.now(UTC), COLLECTED_CANDLE_PERIOD)
        source.candles.add_candles("A", make_candle(last - timedelta(minutes=8)))
        assert (await run_pass_cli(kalshi_db, source, capsys))[0] == cmd.EXIT_OK
        # Roll the watermark back so the second pass re-requests the window,
        # and add one candle inside it.
        await kalshi_conn.execute(
            "UPDATE kalshi.market_candle_state "
            "SET watermark_ts = watermark_ts - interval '10 minutes'"
        )
        source.candles.add_candles("A", make_candle(last - timedelta(minutes=4)))
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK
        candles = summary["phases"][1]["summary"]
        assert candles["candles_fetched"] == 2
        assert candles["candles_written"] == 1
        assert await _duplicates(kalshi_conn) == []
        assert len(await _candle_rows(kalshi_conn)) == 2

    async def test_closed_market_completes_and_is_not_requested_again(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 7: watermark reaches close_time + period, the market
        counts as complete through close, and the next pass leaves it be."""
        from manta_trading.data.kalshi.status import read_candle_status

        source = _source()
        source.add_settled(_traded_settled("DONE", timedelta(minutes=30)))
        assert (await run_pass_cli(kalshi_db, source, capsys))[0] == cmd.EXIT_OK
        state = await _state(kalshi_conn)
        close_time = (
            await column(
                kalshi_conn,
                "SELECT close_time FROM kalshi.markets WHERE ticker = 'DONE'",
            )
        )[0]
        assert state["DONE"] >= close_time + timedelta(minutes=1)
        with psycopg.connect(kalshi_db) as conn:
            status = read_candle_status(conn, _settings(kalshi_db).collection_rule())
        assert status is not None and status.complete_through_close == 1
        requests_before = len(source.candles.candle_queries)
        assert (await run_pass_cli(kalshi_db, source, capsys))[0] == cmd.EXIT_OK
        later = source.candles.candle_queries[requests_before:]
        assert all("DONE" not in q["tickers"] for q in later)  # type: ignore[operator]

    async def test_backlog_is_capped_and_drains_oldest_first(
        self,
        kalshi_db: str,
        kalshi_conn: psycopg.AsyncConnection[Any],
        capsys,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Criterion 8: one backlog row per pass here; the older settlement
        goes first and ``backlog_remaining`` falls to zero on the second."""
        from manta_trading.data.kalshi import candle_sync

        monkeypatch.setattr(candle_sync, "BACKLOG_ROWS_PER_PASS", 1)
        source = _source()
        source.add_settled(
            _traded_settled("OLDER", timedelta(hours=5)),
            _traded_settled("NEWER", timedelta(hours=1)),
        )
        code, first = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK
        assert set(await _state(kalshi_conn)) == {"OLDER"}
        assert first["phases"][1]["summary"]["pending"]["backlog_remaining"] == 1
        code, second = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK
        assert set(await _state(kalshi_conn)) == {"OLDER", "NEWER"}
        assert second["phases"][1]["summary"]["pending"]["backlog_remaining"] == 0

    async def test_omitted_ticker_is_exit_three_with_no_state_row(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 10."""
        source = _source()
        source.add_live(
            MarketStatusFilter.OPEN, _traded_live("GONE"), _traded_live("OK")
        )
        source.candles.omit.add("GONE")
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_SYNC_PARTIAL
        assert summary["outcome"] == "partial"
        assert summary["phases"][1]["outcome"] == "partial"
        assert summary["phases"][1]["summary"]["item_errors"] == [
            {"ticker": "GONE", "reason": "not served by the batch endpoint"}
        ]
        assert set(await _state(kalshi_conn)) == {"OK"}
