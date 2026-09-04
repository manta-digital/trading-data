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
from kalshi_helpers import apply_kalshi_track, column, write_catalog
from kalshi_support.fake_candle_source import make_candle
from kalshi_support.fake_source import FakeCatalogSource, make_market
from kalshi_support.fake_trade_source import make_trade
from psycopg import sql
from test_kalshi_sync import EVENT, _live, _settings, _settled, _source

from manta_trading.cli.commands import kalshi as cmd
from manta_trading.data.kalshi.candle_plan import last_complete_period
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    COLLECTED_CANDLE_PERIOD,
    HISTORICAL_PHASE_MINUTES,
    HISTORICAL_TRADES_FLOOR,
    SYNC_ADVISORY_LOCK_KEY,
    MarketStatus,
    MarketStatusFilter,
    Surface,
)
from manta_trading.data.kalshi.db import LOCK_HELD, open_sync_connection
from manta_trading.data.kalshi.repository import CatalogRepository
from manta_trading.data.kalshi.sync_types import SyncOutcome
from manta_trading.data.kalshi.trade_repository import TradeRepository
from manta_trading.data.kalshi.trade_status import read_trade_status
from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)
from manta_trading.providers.types import RateLimit

#: The tables the two commands must leave identical (Criterion 1).
_CATALOG_TABLES = ("series", "events", "markets")
#: Slice 265: the trades phase drains from the cutoff, so the fake's cutoff
#: sits this far back — three one-hour windows prove the walk without two
#: months of empty tape per pass.
_TRADES_CUTOFF_AGO = timedelta(hours=3)


#: Slice 267: the historical phase's cap derives from the client budget
#: (``requests_per_minute × HISTORICAL_PHASE_MINUTES``). One request a minute
#: gives a cap of 30, so a pass whose live floor is the fake cutoff descends a
#: couple of dozen empty hours instead of walking to 2026-01-01.
_PASS_BUDGET = RateLimit(requests_per_minute=1)
_HISTORICAL_CAP = _PASS_BUDGET.requests_per_minute * HISTORICAL_PHASE_MINUTES


def _pass_source(page_size: int | None = None) -> FakeCatalogSource:
    """``_source`` with the trades cutoff ``_TRADES_CUTOFF_AGO`` back."""
    source = _source(page_size)
    cutoff = datetime.now(UTC).replace(microsecond=0) - _TRADES_CUTOFF_AGO
    source.cutoff = source.cutoff.model_copy(update={"trades_created_ts": cutoff})
    source.rate_limit = _PASS_BUDGET
    return source


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
        source = _pass_source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"), _live("B"), _live("C"))
        source.add_settled(_settled("S-A", timedelta(minutes=30)))
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK, summary
        assert summary["exit_code"] == 0 and summary["outcome"] == "ok"
        assert summary["phases"][0]["name"] == "catalog"
        assert summary["phases"][0]["outcome"] == "ok"
        catalog = summary["phases"][0]["summary"]
        assert catalog["phases"]["markets"]["written"] == 3
        # Every phase wrote its surface's row (264 candlesticks, 265 trades,
        # 267 historical — seeded from the live floor in the same pass).
        assert await _sync_state(kalshi_conn) == [
            "(candlesticks,t,t)",
            "(catalog,t,t)",
            "(historical,t,t)",
            "(trades,t,t)",
        ]
        historical = summary["phases"][3]["summary"]
        assert historical["cap"] == _HISTORICAL_CAP and historical["capped"] is True
        assert source.closed is True

    async def test_second_pass_writes_nothing(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Write-on-change holds through the pass, as it does through sync."""
        source = _pass_source()
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
        source = _pass_source()
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
            "phase_finished",  # the trades phase (slice 265)
            "phase_finished",  # the historical phase (slice 267)
            "pass_finished",
        ]
        assert events[-4]["phase"] == "candles"
        assert events[-3]["phase"] == "trades"
        assert events[-2]["phase"] == "historical"
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
            source = _pass_source()
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
            with patch.object(
                KalshiClient, "from_settings", return_value=_pass_source()
            ):
                code = await cmd.run_pass(_settings(kalshi_db), None, True)
        finally:
            await holder.close()
        assert code == cmd.EXIT_PREFLIGHT
        assert LOCK_HELD in capsys.readouterr().err

    async def test_lock_is_released_after_a_pass(self, kalshi_db: str, capsys):
        assert (await run_pass_cli(kalshi_db, _pass_source(), capsys))[0] == cmd.EXIT_OK
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
        source = _pass_source()
        source.add_live(
            MarketStatusFilter.OPEN, _traded_live("A"), _traded_live("IDLE")
        )
        last = last_complete_period(datetime.now(UTC), COLLECTED_CANDLE_PERIOD)
        source.candles.add_candles(
            "A", make_candle(last - timedelta(minutes=2)), make_candle(last)
        )
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK, summary
        assert [p["name"] for p in summary["phases"]] == [
            "catalog",
            "candles",
            "trades",
            "historical",
        ]
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
        source = _pass_source()
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

        source = _pass_source()
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
        source = _pass_source()
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
        source = _pass_source()
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


# ---------------------------------------------------------------------------
# All three phases end to end (slice 265, Task 6.1)
# ---------------------------------------------------------------------------


async def _trade_rows(conn: psycopg.AsyncConnection[Any]) -> list[Any]:
    return await column(
        conn, "SELECT market_ticker FROM kalshi.trades ORDER BY created_time"
    )


async def _trade_duplicates(conn: psycopg.AsyncConnection[Any]) -> list[Any]:
    return await column(
        conn,
        "SELECT market_ticker FROM kalshi.trades "
        "GROUP BY market_ticker, created_time, trade_id HAVING count(*) > 1",
    )


async def _trade_state(conn: psycopg.AsyncConnection[Any]) -> tuple[Any, Any]:
    cursor = await conn.execute(
        "SELECT watermark_ts, coverage_from_ts FROM kalshi.sync_state "
        "WHERE surface = %s",
        (Surface.TRADES.value,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row[0], row[1]


class TestThreePhasePass:
    def _seeded(self) -> tuple[FakeCatalogSource, datetime]:
        source = _pass_source()
        source.add_live(MarketStatusFilter.OPEN, _traded_live("A"))
        cutoff = source.cutoff.trades_created_ts
        source.trades.add_trades(
            make_trade("A", cutoff + timedelta(minutes=10)),
            make_trade("KXMVE-X", cutoff + timedelta(minutes=30)),
            make_trade("A", cutoff + timedelta(minutes=70)),
        )
        return source, cutoff

    async def test_three_phases_in_order_and_the_tape_is_stored(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 1: catalog, candles, trades; the trades phase drains from
        the cutoff through the catalog walk's start, storing what the rule
        selects and counting the rest."""
        source, cutoff = self._seeded()
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK, summary
        assert [p["name"] for p in summary["phases"]] == [
            "catalog",
            "candles",
            "trades",
            "historical",
        ]
        trades = summary["phases"][2]["summary"]
        assert trades["trades_fetched"] == 3
        assert trades["trades_written"] == 2
        assert trades["unknown_market"] == 1
        assert trades["excluded_by_rule"] == 0 and trades["duplicates"] == 0
        assert trades["unknown_prefixes"] == {"KXMVE": 1}
        assert trades["windows_completed"] == 3 and trades["capped"] is False
        assert trades["coverage_from"] == cutoff.isoformat()
        assert await _trade_rows(kalshi_conn) == ["A", "A"]
        watermark, coverage_from = await _trade_state(kalshi_conn)
        assert coverage_from == cutoff
        assert watermark > cutoff + timedelta(hours=2)

    async def test_second_pass_writes_nothing_and_reports_duplicates(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 3: the re-walked range is fetched again, written never."""
        source, cutoff = self._seeded()
        assert (await run_pass_cli(kalshi_db, source, capsys))[0] == cmd.EXIT_OK
        # Roll the watermark back to inside the tape so the second pass
        # re-walks a range that holds a stored trade (the +70 min one).
        await kalshi_conn.execute(
            "UPDATE kalshi.sync_state SET watermark_ts = %s WHERE surface = %s",
            (cutoff + timedelta(hours=1), Surface.TRADES.value),
        )
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK
        trades = summary["phases"][2]["summary"]
        assert trades["trades_fetched"] == 1
        assert trades["trades_written"] == 0
        assert trades["duplicates"] == 1
        assert await _trade_rows(kalshi_conn) == ["A", "A"]
        assert await _trade_duplicates(kalshi_conn) == []

    async def test_mid_window_abort_leaves_the_watermark_and_keeps_the_pages(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 4 on a real connection: pages 1–2 are committed in their
        own transactions; the watermark write never happens."""
        source = _pass_source()
        source.add_live(MarketStatusFilter.OPEN, _traded_live("A"))
        cutoff = source.cutoff.trades_created_ts
        source.trades.page_size = 1
        source.trades.add_trades(
            *(make_trade("A", cutoff + timedelta(minutes=i)) for i in range(1, 5))
        )
        source.trades.raise_on("get_trades", ProviderTransientError("503"), at=3)
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_PROVIDER
        assert [p["outcome"] for p in summary["phases"]] == [
            str(SyncOutcome.OK),
            str(SyncOutcome.OK),
            str(SyncOutcome.PROVIDER_ABORT),
            "skipped",
        ]
        assert "503" in summary["phases"][2]["summary"]["error"]
        assert len(await _trade_rows(kalshi_conn)) == 2
        watermark, coverage_from = await _trade_state(kalshi_conn)
        assert watermark == cutoff and coverage_from == cutoff
        # The earlier phases' rows stand.
        assert await _sync_state(kalshi_conn, Surface.CATALOG) == ["(catalog,t,t)"]


# ---------------------------------------------------------------------------
# Four phases (slice 267, Task 8.1): the archive walk feeds the catalog, the
# behind-cutoff market gets its candles, the archived tape is walked down to
# the floor, and ``status`` measures coverage from the effective floor.
# ---------------------------------------------------------------------------

FLOOR = HISTORICAL_TRADES_FLOOR
#: The seeded live floor: three hours above the constant floor, so the
#: descent is three windows and reaches it in one firing.
LIVE_FLOOR = FLOOR + timedelta(hours=3)


def _archived(ticker: str, settled: datetime) -> Any:
    """A finalized market rule C selects, served by the archive only."""
    return make_market(
        ticker,
        EVENT,
        status=MarketStatus.FINALIZED.value,
        result="yes",
        open_time=settled - timedelta(days=1),
        close_time=settled - timedelta(minutes=1),
        settlement_ts=settled,
        volume_24h_fp="0.00",
        volume_fp="50.00",
    )


async def _seed_four_phases(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
) -> tuple[FakeCatalogSource, Any]:
    """The Task 8.1 fixtures: an archive page of markets the catalog does not
    know (settled before the floor minus the margin), three hours of archived
    tape below the seeded live floor with trades for them, one behind-cutoff
    market with candles, and the live trades row seeded at that floor."""
    source = _pass_source()
    source.add_live(MarketStatusFilter.OPEN, _traded_live("A"))
    cutoff = source.cutoff.trades_created_ts
    source.trades.add_trades(make_trade("A", cutoff + timedelta(minutes=10)))
    before_stop = FLOOR - timedelta(days=2)
    source.historical.add_archive_page(
        _archived("ARC1", before_stop), _archived("ARC2", before_stop - timedelta(1))
    )
    source.historical.add_trades(
        make_trade("ARC1", FLOOR + timedelta(minutes=30)),
        make_trade("KXMVE-X", FLOOR + timedelta(minutes=45)),
        make_trade("ARC2", FLOOR + timedelta(minutes=90)),
        make_trade("A", FLOOR + timedelta(minutes=150)),
    )
    behind = _archived("H", source.cutoff.market_settled_ts - timedelta(days=1))
    behind = behind.model_copy(update={"open_time": behind.close_time - timedelta(2)})
    await write_catalog(CatalogRepository(kalshi_conn), [behind])
    source.historical.add_candles(
        "H",
        make_candle(behind.open_time + timedelta(minutes=1)),
        make_candle(behind.close_time),
    )
    rule = _settings(kalshi_db).collection_rule()
    live = TradeRepository(kalshi_conn, rule, trades_excluded=frozenset())
    async with live.transaction():
        await live.init_state(cutoff, LIVE_FLOOR)
    return source, behind


async def _historical_row(conn: psycopg.AsyncConnection[Any]) -> tuple[Any, ...]:
    cursor = await conn.execute(
        "SELECT watermark_ts, coverage_from_ts, cursor FROM kalshi.sync_state "
        "WHERE surface = %s",
        (Surface.HISTORICAL.value,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


class TestFourPhasePass:
    async def test_first_pass_walks_the_archive_drains_and_reaches_the_floor(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source, behind = await _seed_four_phases(kalshi_db, kalshi_conn)
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK, summary
        assert [(p["name"], p["outcome"]) for p in summary["phases"]] == [
            ("catalog", "ok"),
            ("candles", "ok"),
            ("trades", "ok"),
            ("historical", "ok"),
        ]
        hist = summary["phases"][3]["summary"]
        # Criterion 9: the archive page's markets and their parents are in
        # the catalog, and the archived tape's trades for them were written.
        assert await column(
            kalshi_conn,
            "SELECT ticker FROM kalshi.markets WHERE ticker LIKE 'ARC%%' ORDER BY 1",
        ) == ["ARC1", "ARC2"]
        assert hist["archive"]["walked"] is True and hist["archive"]["pages"] == 1
        assert hist["archive"]["markets_written"] == 2
        assert await _trade_rows(kalshi_conn) == ["ARC1", "ARC2", "A", "A"]
        assert hist["unknown_market"] == 1 and hist["unknown_prefixes"] == {"KXMVE": 1}
        # Criterion 4: the identity on the phase's counts.
        assert (
            hist["trades_fetched"]
            == 4
            == (
                hist["trades_written"]
                + hist["unknown_market"]
                + hist["excluded_by_rule"]
                + hist["duplicates"]
            )
        )
        # Criterion 2/3: the row was seeded at the live floor with the floor
        # target, descended by whole hours, and reached the floor.
        assert hist["watermark"] == {
            "before": LIVE_FLOOR.isoformat(),
            "after": FLOOR.isoformat(),
        }
        assert hist["windows_completed"] == 3 and hist["floor_reached"] is True
        assert await _historical_row(kalshi_conn) == (FLOOR, FLOOR, None)
        # Criterion 5: the behind-cutoff market has rows and a state row and
        # left the set. The two archive-walked markets joined the set in the
        # same firing (Decision 9's consequence) and were stamped too — the
        # archive serves no candles for them, so no rows, but a state row.
        candles = summary["phases"][1]["summary"]
        assert candles["pending"]["backlog"] == 0
        assert hist["candles"]["markets_completed"] == 3
        assert hist["candles"]["candles_written"] == 2
        assert hist["candles"]["markets_remaining"] == 0
        assert [r for r in await _candle_rows(kalshi_conn) if r.startswith("(H,")]
        assert not [r for r in await _candle_rows(kalshi_conn) if "ARC" in r]
        state = await _state(kalshi_conn)
        assert set(state) - {"A"} == {"ARC1", "ARC2", "H"}  # A: the live phase's
        assert state["H"] == behind.close_time + timedelta(
            minutes=int(COLLECTED_CANDLE_PERIOD)
        )
        # Criterion 7: status measures coverage from the effective floor.
        with psycopg.connect(kalshi_db) as conn:
            status = read_trade_status(
                conn, _settings(kalshi_db).collection_rule(), frozenset()
            )
        assert status is not None
        assert status.coverage_from == FLOOR
        assert status.before_coverage == 2  # ARC1, ARC2 closed before the floor
        assert (
            status.before_coverage
            + status.short_of_close
            + status.partial_history
            + status.complete_through_close
        ) == 3  # + H; A is still open

    async def test_second_pass_writes_nothing_and_stays_at_the_floor(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source, _ = await _seed_four_phases(kalshi_db, kalshi_conn)
        assert (await run_pass_cli(kalshi_db, source, capsys))[0] == cmd.EXIT_OK
        live_before = await _trade_state(kalshi_conn)
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK, summary
        hist = summary["phases"][3]["summary"]
        assert hist["trades_written"] == 0 and hist["candles"]["candles_written"] == 0
        assert hist["archive"] == {
            "walked": True,
            "pages": 0,
            "markets_fetched": 0,
            "markets_written": 0,
            "restarted": False,
        }
        assert hist["floor_reached"] is True and hist["windows_completed"] == 0
        assert hist["requests"] == 0
        assert await _historical_row(kalshi_conn) == (FLOOR, FLOOR, None)
        # The live row's floor is untouched (its watermark follows the live
        # walk); nothing was written twice.
        assert (await _trade_state(kalshi_conn))[1] == live_before[1] == LIVE_FLOOR
        assert await _trade_rows(kalshi_conn) == ["ARC1", "ARC2", "A", "A"]
        assert await _trade_duplicates(kalshi_conn) == []
        assert await _duplicates(kalshi_conn) == []

    async def test_transient_candle_error_aborts_and_leaves_the_earlier_phases(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source, _ = await _seed_four_phases(kalshi_db, kalshi_conn)
        source.historical.raise_on(
            "get_historical_market_candlesticks", ProviderTransientError("503")
        )
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_BY_OUTCOME[SyncOutcome.PROVIDER_ABORT]
        assert [p["outcome"] for p in summary["phases"]] == [
            "ok",
            "ok",
            "ok",
            str(SyncOutcome.PROVIDER_ABORT),
        ]
        assert "503" in summary["phases"][3]["summary"]["error"]
        assert await _sync_state(kalshi_conn, Surface.CATALOG) == ["(catalog,t,t)"]
        assert await _sync_state(kalshi_conn, Surface.TRADES) == ["(trades,t,t)"]
        # The walk completed (row exists, cursor cleared) but the tape was
        # never seeded or descended; only the live phase's trade stands.
        assert await _historical_row(kalshi_conn) == (None, None, None)
        assert await _trade_rows(kalshi_conn) == ["A"]

    async def test_permanent_candle_error_is_partial_and_the_tape_still_descends(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        """Criterion 10."""
        source, _ = await _seed_four_phases(kalshi_db, kalshi_conn)
        source.historical.raise_on(
            "get_historical_market_candlesticks",
            ProviderPermanentError("404"),
            when=lambda query: query["ticker"] == "H",
        )
        code, summary = await run_pass_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_BY_OUTCOME[SyncOutcome.PARTIAL]
        assert [p["outcome"] for p in summary["phases"]] == [
            "ok",
            "ok",
            "ok",
            str(SyncOutcome.PARTIAL),
        ]
        hist = summary["phases"][3]["summary"]
        assert hist["item_errors"] == [{"ticker": "H", "reason": "404"}]
        assert set(await _state(kalshi_conn)) - {"A"} == {"ARC1", "ARC2"}  # not H
        assert hist["candles"]["markets_completed"] == 2
        assert hist["candles"]["markets_remaining"] == 1
        assert await _historical_row(kalshi_conn) == (FLOOR, FLOOR, None)
        assert await _trade_rows(kalshi_conn) == ["ARC1", "ARC2", "A", "A"]
