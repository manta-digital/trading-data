"""Integration tests: the catalog sync end to end on a throwaway database
(slice 262, Tasks 8.2, 8.4, 8.5).

Fake source, real repository, real connection preflight — only
``ephemeral_db`` (``MT_TIMESCALE_TEST_URL``), never the production URL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from kalshi_helpers import column
from kalshi_support.fake_source import (
    FakeCatalogSource,
    make_event,
    make_market,
    make_series,
)
from typer.testing import CliRunner

from manta_trading.cli.commands import kalshi as cmd
from manta_trading.constants import DB_BULK_SESSION
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    SETTLED_WINDOW,
    SYNC_ADVISORY_LOCK_KEY,
    MarketStatus,
    MarketStatusFilter,
)
from manta_trading.data.kalshi.db import (
    LOCK_HELD,
    TRACK_NOT_APPLIED,
    PreflightError,
    open_sync_connection,
)
from manta_trading.data.kalshi.sync_types import epoch
from manta_trading.providers.errors import ProviderTransientError

# ---------------------------------------------------------------------------
# Task 8.2 — preflight
# ---------------------------------------------------------------------------


class TestPreflight:
    async def test_session_settings_applied_and_lock_taken(self, kalshi_db: str):
        conn = await open_sync_connection(kalshi_db)
        try:
            # SHOW normalizes units ("300s" → "5min"); compare in seconds.
            cursor = await conn.execute(
                "SELECT extract(epoch FROM "
                "current_setting('statement_timeout')::interval)"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert int(row[0]) == int(DB_BULK_SESSION.statement_timeout.rstrip("s"))
            cursor = await conn.execute("SHOW timezone")
            assert await cursor.fetchone() == ("UTC",)
            assert conn.autocommit is True
            cursor = await conn.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND objid = %s AND pid = pg_backend_pid()",
                (SYNC_ADVISORY_LOCK_KEY,),
            )
            assert await cursor.fetchone() == (1,)
        finally:
            await conn.close()

    async def test_unmigrated_database_names_the_track(self, ephemeral_db: str):
        with pytest.raises(PreflightError, match="kalshi track"):
            await open_sync_connection(ephemeral_db)
        assert "kalshi track" in TRACK_NOT_APPLIED

    async def test_held_lock_is_refused(self, kalshi_db: str):
        holder = await open_sync_connection(kalshi_db)
        try:
            with pytest.raises(PreflightError, match=LOCK_HELD):
                await open_sync_connection(kalshi_db)
        finally:
            await holder.close()
        # Closing the holder releases the lock: the next preflight succeeds.
        again = await open_sync_connection(kalshi_db)
        await again.close()

    async def test_unreachable_database(self):
        with pytest.raises(PreflightError, match="unreachable"):
            await open_sync_connection(
                "postgresql://nobody:none@127.0.0.1:1/nothing?connect_timeout=1"
            )


async def _pid(conn: psycopg.AsyncConnection[Any]) -> int:
    cursor = await conn.execute("SELECT pg_backend_pid()")
    row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# Tasks 8.4 / 8.5 — end to end through the CLI's run path
# ---------------------------------------------------------------------------

SERIES, EVENT = "S1", "E1"


def _settings(url: str) -> MagicMock:
    from manta_trading.config import Settings

    settings = MagicMock()
    settings.timescale_db_url = url
    settings.kalshi_api_key_id = None
    settings.kalshi_private_key_path = None
    settings.kalshi_requests_per_minute = None
    # The candle phase (slice 264) reads the collection rule off the run's
    # settings; the tier's environment carries no MT_KALSHI_CANDLE_*, so
    # this is rule C — the real default, not a re-spelling of it.
    settings.candle_rule.return_value = Settings(_env_file=None).candle_rule()  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
    return settings


def _source(page_size: int | None = None) -> FakeCatalogSource:
    source = FakeCatalogSource(page_size=page_size, load_fixtures=False)
    source.add_series(make_series(SERIES))
    source.add_events(make_event(EVENT, SERIES))
    return source


def _live(
    ticker: str, *, status: str = "active", close_in: timedelta = timedelta(days=1)
):
    return make_market(
        ticker,
        EVENT,
        status=status,
        close_time=datetime.now(UTC) + close_in,
        result=None,
        settlement_ts=None,
    )


def _settled(ticker: str, settled_ago: timedelta):
    ts = datetime.now(UTC) - settled_ago
    return make_market(
        ticker,
        EVENT,
        status=MarketStatus.FINALIZED.value,
        result="yes",
        close_time=ts - timedelta(minutes=1),
        settlement_ts=ts,
    )


async def run_cli(
    kalshi_db: str,
    source: object,
    capsys: pytest.CaptureFixture[str],
    *,
    settled_since: datetime | None = None,
    events_file: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """``run_sync`` exactly as the command calls it, with the client swapped."""
    with patch.object(KalshiClient, "from_settings", return_value=source):
        code = await cmd.run_sync(
            _settings(kalshi_db), settled_since, events_file, True
        )
    return code, json.loads(capsys.readouterr().out)


class TestEndToEnd:
    async def test_first_run_populates_and_sets_state(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"), _live("B"), _live("C"))
        source.add_settled(_settled("S-A", timedelta(minutes=30)))
        code, summary = await run_cli(
            kalshi_db,
            source,
            capsys,
            settled_since=datetime.now(UTC) - timedelta(hours=2),
        )
        assert code == cmd.EXIT_OK, summary
        assert summary["phases"]["markets"]["written"] == 3
        assert summary["phases"]["settled"]["written"] == 1
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.series") == [1]
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.events") == [1]
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.markets") == [4]
        state = await column(
            kalshi_conn,
            "SELECT last_full_sync_at IS NOT NULL AND watermark_ts IS NOT NULL "
            "FROM kalshi.sync_state WHERE surface = 'catalog'",
        )
        assert state == [True]
        assert source.closed is True

    async def test_second_identical_run_writes_nothing(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"), _live("B"))
        since = datetime.now(UTC) - timedelta(hours=1)
        assert (await run_cli(kalshi_db, source, capsys, settled_since=since))[0] == 0
        before = await column(
            kalshi_conn, "SELECT first_seen_at FROM kalshi.markets ORDER BY ticker"
        )
        code, summary = await run_cli(kalshi_db, source, capsys, settled_since=since)
        assert code == cmd.EXIT_OK
        assert [
            summary["phases"][p]["written"] for p in ("series", "markets", "events")
        ] == [0, 0, 0]
        after = await column(
            kalshi_conn, "SELECT first_seen_at FROM kalshi.markets ORDER BY ticker"
        )
        assert after == before
        assert await column(
            kalshi_conn,
            "SELECT count(*) FROM kalshi.markets WHERE last_synced_at > first_seen_at",
        ) == [0]

    async def test_awaiting_lifecycle(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source = _source()
        since = datetime.now(UTC) - timedelta(hours=1)
        past = _live("PAST", close_in=-timedelta(hours=2))
        vanish = _live("VAN", status="closed", close_in=-timedelta(hours=3))
        source.add_live(MarketStatusFilter.OPEN, past)
        source.add_live(MarketStatusFilter.CLOSED, vanish)
        code, summary = await run_cli(kalshi_db, source, capsys, settled_since=since)
        assert code == 0 and summary["awaiting"]["entered"] == 2
        assert await column(
            kalshi_conn,
            "SELECT market_ticker FROM kalshi.awaiting_settlement ORDER BY 1",
        ) == ["PAST", "VAN"]

        # Run 2: PAST arrives finalized on the settled stream; VAN vanishes from
        # the walk and must be looked up by ticker.
        source.live[MarketStatusFilter.OPEN].clear()
        source.live[MarketStatusFilter.CLOSED].clear()
        source.add_settled(_settled("PAST", timedelta(minutes=10)))
        code, summary = await run_cli(kalshi_db, source, capsys, settled_since=since)
        assert code == 0
        assert summary["awaiting"] == {
            "entered": 0,
            "retired": 1,
            "checked": 1,
            "unreachable": 0,
        }
        assert await column(
            kalshi_conn,
            "SELECT status, result FROM kalshi.markets WHERE ticker = 'PAST'",
        ) == ["finalized"]
        assert await column(
            kalshi_conn,
            "SELECT market_ticker, last_checked_at IS NOT NULL "
            "FROM kalshi.awaiting_settlement",
        ) == ["VAN"]
        checked = await column(
            kalshi_conn, "SELECT last_checked_at FROM kalshi.awaiting_settlement"
        )
        assert checked[0] is not None
        lookups = [q for q in source.markets_queries if q.get("tickers")]
        assert [q["tickers"] for q in lookups] == ["VAN"]

    async def test_interrupted_drain_resumes_without_gap_or_duplicates(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source = _source()
        floor = datetime.now(UTC) - 3 * SETTLED_WINDOW + timedelta(minutes=5)
        for i in range(6):
            source.add_settled(
                _settled(f"W{i}", 3 * SETTLED_WINDOW - timedelta(hours=1 + 3 * i))
            )
        third_window_min = epoch(floor + 2 * SETTLED_WINDOW - timedelta(seconds=1))
        source.raise_on(
            "get_markets",
            ProviderTransientError("503"),
            when=lambda q: q.get("min_settled_ts") == third_window_min,
        )
        code, summary = await run_cli(kalshi_db, source, capsys, settled_since=floor)
        assert code == cmd.EXIT_PROVIDER
        assert summary["windows_completed"] == 2 and summary["error"].startswith(
            "Provider"
        )
        watermark = await column(
            kalshi_conn, "SELECT watermark_ts FROM kalshi.sync_state"
        )
        assert watermark == [floor + 2 * SETTLED_WINDOW]
        assert await column(
            kalshi_conn, "SELECT last_full_sync_at FROM kalshi.sync_state"
        ) == [None]
        first_pass = await column(kalshi_conn, "SELECT count(*) FROM kalshi.markets")

        source._failures.clear()
        code, summary = await run_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_OK
        resumed = [
            q for q in source.markets_queries if q.get("min_settled_ts") is not None
        ][-summary["windows_completed"] :]
        assert resumed[0]["min_settled_ts"] == third_window_min, "resumed at window 3"
        rows = await column(
            kalshi_conn, "SELECT ticker FROM kalshi.markets ORDER BY ticker"
        )
        assert rows == [f"W{i}" for i in range(6)], (first_pass, rows)

    async def test_events_file_is_valid_jsonl(
        self, kalshi_db: str, tmp_path: Path, capsys
    ):
        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"))
        path = tmp_path / "events.jsonl"
        code, _ = await run_cli(
            kalshi_db,
            source,
            capsys,
            settled_since=datetime.now(UTC) - timedelta(hours=1),
            events_file=path,
        )
        assert code == 0
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert [line["event_type"] for line in lines] == [
            "run_started",
            *(["phase_finished"] * 5),
            "run_finished",
        ]
        assert [line["phase"] for line in lines[1:6]] == [
            "series",
            "markets",
            "events",
            "settled",
            "awaiting",
        ]

    def test_status_after_first_run(self, kalshi_db: str, capsys):
        import asyncio

        source = _source()
        source.add_live(MarketStatusFilter.OPEN, _live("A"))
        code, _ = asyncio.run(
            run_cli(
                kalshi_db,
                source,
                capsys,
                settled_since=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        assert code == 0
        from manta_trading.cli.app import app

        with (
            patch("manta_trading.cli.app.Settings", return_value=_settings(kalshi_db)),
            patch("manta_trading.cli.app.setup_logging"),
        ):
            result = CliRunner().invoke(app, ["data", "kalshi", "status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["synced"] is True
        assert payload["series"] == 1 and payload["markets_by_status"]["active"] == 1

    async def test_provider_abort_through_real_client(
        self,
        kalshi_db: str,
        kalshi_conn: psycopg.AsyncConnection[Any],
        tmp_path: Path,
        capsys,
    ):
        """Review F003: httpx → 261's transient mapping → exit 2, end to end."""
        client = KalshiClient(base_url="http://127.0.0.1:1", max_retries=0)
        path = tmp_path / "events.jsonl"
        code, summary = await run_cli(kalshi_db, client, capsys, events_file=path)
        assert code == cmd.EXIT_PROVIDER
        assert summary["outcome"] == "provider_abort"
        last = json.loads(path.read_text().splitlines()[-1])
        assert last["event_type"] == "run_finished" and last["error"]
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.sync_state") == [
            0
        ]


class TestStorageFailureProofs:
    async def test_out_of_vocabulary_status_is_one_item_error(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source = _source()
        source.add_live(
            MarketStatusFilter.OPEN,
            _live("OK1"),
            _live("BAD", status="bogus"),
            _live("OK2"),
        )
        code, summary = await run_cli(
            kalshi_db,
            source,
            capsys,
            settled_since=datetime.now(UTC) - timedelta(hours=1),
        )
        assert code == cmd.EXIT_SYNC_PARTIAL
        assert [e["ticker"] for e in summary["item_errors"]] == ["BAD"]
        assert "CheckViolation" in summary["item_errors"][0]["reason"]
        assert await column(
            kalshi_conn, "SELECT ticker FROM kalshi.markets ORDER BY 1"
        ) == ["OK1", "OK2"]

    async def test_backend_terminated_mid_walk(
        self, kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any], capsys
    ):
        source = _source()
        source.add_live(MarketStatusFilter.UNOPENED, _live("U1", status="initialized"))
        source.add_live(MarketStatusFilter.OPEN, _live("A"), _live("B"))
        original = source.get_markets

        async def get_markets_then_kill(*, cursor=None, **query):  # type: ignore[no-untyped-def]
            if query.get("status") is MarketStatusFilter.OPEN:
                # The unopened page is committed; kill the run's backend
                # (the one holding the advisory lock) before the open page.
                with psycopg.connect(kalshi_db, autocommit=True) as admin:
                    # pg_locks is cluster-wide: scope to this throwaway database
                    # or the kill reaches a sync on any other database here.
                    admin.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND objid = %s "
                        "AND database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database())",
                        (SYNC_ADVISORY_LOCK_KEY,),
                    )
            return await original(cursor=cursor, **query)

        source.get_markets = get_markets_then_kill  # type: ignore[method-assign]
        code, summary = await run_cli(kalshi_db, source, capsys)
        assert code == cmd.EXIT_STORAGE
        # AdminShutdown is the OperationalError subclass psycopg raises here.
        assert summary["outcome"] == "storage_abort"
        assert "terminating connection" in summary["error"]
        assert await column(kalshi_conn, "SELECT ticker FROM kalshi.markets") == ["U1"]
        assert await column(kalshi_conn, "SELECT count(*) FROM kalshi.sync_state") == [
            0
        ]
