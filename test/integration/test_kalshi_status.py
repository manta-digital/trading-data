"""Integration tests: ``read_catalog_status`` on a throwaway database (slice 262).

Seeds through the Section 3 repository so rows are real served shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from kalshi_helpers import market_rows, parent_events, parent_series

from manta_trading.data.kalshi.constants import MarketStatus, Surface
from manta_trading.data.kalshi.repository import CatalogRepository
from manta_trading.data.kalshi.status import age_bucket_labels, read_catalog_status


def test_never_synced_is_none(kalshi_db: str):
    with psycopg.connect(kalshi_db) as conn:
        assert read_catalog_status(conn) is None


async def test_histogram_threshold_and_oldest(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    now = datetime.now(UTC)
    ages = {"HALF": 0.5, "THREE": 3, "TEN": 10, "FORTY": 40}
    base = market_rows("markets_open")[0]
    markets = [
        base.model_copy(
            update={
                "ticker": ticker,
                "status": MarketStatus.CLOSED.value,
                "close_time": now - timedelta(days=days),
            }
        )
        for ticker, days in ages.items()
    ]
    repo = CatalogRepository(kalshi_conn)
    async with repo.transaction():
        events = parent_events(markets)
        await repo.upsert_series(parent_series(events))
        await repo.upsert_events(events)
        await repo.upsert_markets(markets)
        await repo.enter_awaiting(now)
        await repo.mark_checked(["TEN"], now)
        await repo.set_last_full_sync(Surface.CATALOG, now)

    with psycopg.connect(kalshi_db) as sync_conn:
        status = read_catalog_status(sync_conn)
    assert status is not None
    assert status.last_full_sync_at == now and status.watermark_ts is None
    assert status.series == 1 and status.events == 1
    assert status.markets_by_status[MarketStatus.CLOSED] == 4
    assert status.markets_by_status[MarketStatus.ACTIVE] == 0
    assert status.awaiting.total == 4
    assert status.awaiting.age_histogram == (1, 1, 1, 1)
    assert status.awaiting.past_threshold == 2
    assert status.awaiting.oldest_ticker == "FORTY"
    assert (
        status.awaiting.oldest_age is not None and status.awaiting.oldest_age.days == 40
    )
    assert status.awaiting.checked_directly == 1
    payload = status.to_dict()
    assert age_bucket_labels() == ["<1d", "1d-7d", "7d-30d", ">30d"]
    assert payload["awaiting_age"] == {"<1d": 1, "1d-7d": 1, "7d-30d": 1, ">30d": 1}
    assert payload["awaiting_past_threshold"] == 2
    assert payload["awaiting_oldest_age_days"] == 40
    assert payload["stuck_threshold_days"] == 7


# ---------------------------------------------------------------------------
# Candle block (slice 264, Task 6.3) — every field, from persisted facts only
# ---------------------------------------------------------------------------


def test_candles_never_collected_is_none(kalshi_db: str):
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.status import read_candle_status

    with psycopg.connect(kalshi_db) as conn:
        assert read_candle_status(conn, RULE_C) is None


async def test_candle_block_every_field(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """Seeds the predicate fixture set (live), a finalized copy behind the
    cutoff, and state rows in every condition the block distinguishes."""
    from test_kalshi_candles import (
        CUTOFF,
        EVERYTHING,
        OPENED,
        RULE_C,
        fixture_markets,
    )

    from manta_trading.data.kalshi.candle_plan import period_span
    from manta_trading.data.kalshi.candle_repository import (
        CandleRepository,
        StateAdvance,
    )
    from manta_trading.data.kalshi.constants import (
        CANDLE_LAG_STALE_AFTER,
        COLLECTED_CANDLE_PERIOD,
    )
    from manta_trading.data.kalshi.status import read_candle_status

    now = datetime.now(UTC)
    span = period_span(COLLECTED_CANDLE_PERIOD)
    live, series = fixture_markets(
        # ``CLOSES`` is a fixed 2026-08-28 instant that SQL ``now()`` has
        # passed; the open set must stay open for this test's counts.
        close_time=now + timedelta(days=1)
    )
    # POLITICS and NULLCAT are selected live; SPORTS is excluded by rule.
    old_finalized, _ = fixture_markets(
        status=MarketStatus.FINALIZED.value,
        close_time=CUTOFF - timedelta(days=2),
        settlement_ts=CUTOFF - timedelta(days=1),
        volume_24h_fp=Decimal(0),
    )
    for market in old_finalized:
        market.ticker = f"OLD-{market.ticker}"
    recent_finalized, _ = fixture_markets(
        status=MarketStatus.FINALIZED.value,
        close_time=now - timedelta(hours=3),
        settlement_ts=now - timedelta(hours=2),
        volume_24h_fp=Decimal(0),
    )
    for market in recent_finalized:
        market.ticker = f"NEW-{market.ticker}"
    catalog = CatalogRepository(kalshi_conn)
    async with catalog.transaction():
        await catalog.upsert_series(series)
        await catalog.upsert_events(parent_events(live))
        await catalog.upsert_markets([*live, *old_finalized, *recent_finalized])
    repo = CandleRepository(kalshi_conn, RULE_C)
    fresh = now - timedelta(minutes=5)
    stale = now - CANDLE_LAG_STALE_AFTER - timedelta(minutes=1)
    new_politics = next(m for m in recent_finalized if m.ticker == "NEW-POLITICS")
    new_nullcat = next(m for m in recent_finalized if m.ticker == "NEW-NULLCAT")
    async with repo.transaction():
        await repo.advance_state(
            COLLECTED_CANDLE_PERIOD,
            [
                # open, selected, fresh — tracked, not lagging, partial history
                StateAdvance("POLITICS", fresh, OPENED + timedelta(hours=1)),
                # open, selected, stale — lagging
                StateAdvance("NULLCAT", stale, OPENED),
                # open, tracked but deselected (Sports) and stale — idle, not lagging
                StateAdvance("SPORTS", stale, OPENED),
                # closed, complete through close
                StateAdvance(
                    "NEW-POLITICS",
                    new_politics.close_time + span,
                    OPENED,
                ),
                # closed, short of close, not behind the cutoff
                StateAdvance("NEW-NULLCAT", new_nullcat.close_time - span, OPENED),
            ],
        )
        await repo.set_sync_state(now, CUTOFF)

    with psycopg.connect(kalshi_db) as conn:
        status = read_candle_status(conn, RULE_C)
    assert status is not None
    assert status.period_minutes == int(COLLECTED_CANDLE_PERIOD)
    assert status.last_phase_at == now
    assert status.cutoff_observed == CUTOFF
    assert status.rule == RULE_C
    assert status.selected_open == 2  # POLITICS, NULLCAT
    assert status.markets_tracked == 5
    assert status.open_lagging == 1  # NULLCAT; SPORTS is deselected, so idle
    assert status.open_oldest_watermark == stale
    assert status.complete_through_close == 1  # NEW-POLITICS
    assert status.closed_short_of_close == 1  # NEW-NULLCAT
    # Backlog: finalized since the cutoff, selected (ever), no state row.
    # Rule C selects NEW-POLITICS and NEW-NULLCAT, and both are tracked.
    assert status.backlog_remaining == 0
    assert status.behind_cutoff_uncollected == 2  # OLD-POLITICS, OLD-NULLCAT
    # Closed, untracked, and not selected: the five OLD-* and five NEW-*
    # markets rule C excludes (Sports, Mentions, patterns, never traded).
    assert status.closed_excluded_by_rule == 10
    assert status.partial_history == 1  # POLITICS coverage after open
    payload = status.to_dict()
    assert payload["rule"]["description"] == RULE_C.describe()
    assert payload["cutoff_observed"] == CUTOFF.isoformat()

    # Changing the rule moves the rule-dependent figures with no collection.
    with psycopg.connect(kalshi_db) as conn:
        everything = read_candle_status(conn, EVERYTHING)
    assert everything is not None
    assert everything.selected_open == 7
    assert everything.closed_excluded_by_rule == 0
    assert everything.behind_cutoff_uncollected == 7
    assert everything.backlog_remaining == 5  # the untracked NEW-* markets
    assert everything.open_lagging == 2  # SPORTS is now selected, and stale
    assert everything.markets_tracked == 5  # unchanged: a persisted fact


async def test_open_market_complete_through_close_is_not_lagging(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """A selected market past its close but not yet finalized, whose watermark
    has reached ``close_time + period``, has nothing left to fetch: it is
    complete through close, never lagging, however old the watermark. The
    2026-08-27 rehearsal counted 409 such markets as lagging."""
    from test_kalshi_candles import CUTOFF, OPENED, RULE_C, fixture_markets

    from manta_trading.data.kalshi.candle_plan import period_span
    from manta_trading.data.kalshi.candle_repository import (
        CandleRepository,
        StateAdvance,
    )
    from manta_trading.data.kalshi.constants import (
        CANDLE_LAG_STALE_AFTER,
        COLLECTED_CANDLE_PERIOD,
    )
    from manta_trading.data.kalshi.status import read_candle_status

    now = datetime.now(UTC)
    span = period_span(COLLECTED_CANDLE_PERIOD)
    closed_at = now - CANDLE_LAG_STALE_AFTER - timedelta(hours=1)
    # Every fixture market closed an hour beyond the stale horizon and awaits
    # determination; rule C selects POLITICS and NULLCAT.
    markets, series = fixture_markets(
        status=MarketStatus.CLOSED.value, close_time=closed_at
    )
    catalog = CatalogRepository(kalshi_conn)
    async with catalog.transaction():
        await catalog.upsert_series(series)
        await catalog.upsert_events(parent_events(markets))
        await catalog.upsert_markets(markets)
    repo = CandleRepository(kalshi_conn, RULE_C)
    async with repo.transaction():
        await repo.advance_state(
            COLLECTED_CANDLE_PERIOD,
            [
                # complete through close: not lagging
                StateAdvance("POLITICS", closed_at + span, OPENED),
                # one period short of close, and stale: lagging
                StateAdvance("NULLCAT", closed_at - span, OPENED),
            ],
        )
        await repo.set_sync_state(now, CUTOFF)

    with psycopg.connect(kalshi_db) as conn:
        status = read_candle_status(conn, RULE_C)
    assert status is not None
    assert status.selected_open == 2  # POLITICS, NULLCAT: closed, not finalized
    assert status.complete_through_close == 1  # POLITICS
    assert status.closed_short_of_close == 1  # NULLCAT
    assert status.open_lagging == 1  # NULLCAT only
    assert status.open_oldest_watermark == closed_at - span


# ---------------------------------------------------------------------------
# Trades block (slice 265, Task 5.3) — every field, from persisted facts only
# ---------------------------------------------------------------------------


def test_trades_never_collected_is_none(kalshi_db: str):
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.trade_status import read_trade_status

    with psycopg.connect(kalshi_db) as conn:
        assert read_trade_status(conn, RULE_C, frozenset()) is None


#: The closed-market fixture set for the four counts: ``(ticker, category,
#: open offset from the coverage floor, close offset from the watermark)`` —
#: ``None`` open is a market with no recorded open. The floor is 30 days back,
#: the watermark one day back, so "after the watermark" is still "closed".
DAY = timedelta(days=1)
TRADE_FIXTURES: list[tuple[str, str, timedelta | None, timedelta]] = [
    ("COMPLETE", "Politics", DAY, -DAY),  # opened after the floor, closed before wm
    ("PARTIAL", "Politics", -5 * DAY, -2 * DAY),  # opened before the floor
    ("OPENNULL", "Politics", None, -DAY),  # no recorded open: partial, never complete
    ("STRADDLE", "Politics", -5 * DAY, timedelta(hours=23)),  # short of close first
    ("SHORT", "Politics", 2 * DAY, timedelta(hours=23)),  # tape not there yet
    ("BEFORE", "Politics", -10 * DAY, -30 * DAY),  # closed before the floor
    ("SPORTS", "Sports", DAY, -DAY),  # excluded by rule C: in none of the four
    ("STILLOPEN", "Politics", DAY, 2 * DAY),  # closes in the future: not closed
]


async def _seed_trade_status(
    kalshi_conn: psycopg.AsyncConnection[Any],
    *,
    coverage_from: datetime,
    watermark: datetime,
    now: datetime,
) -> None:
    from kalshi_helpers import write_catalog
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.trade_repository import TradeRepository

    template = market_rows("markets_open")[0]
    markets = [
        template.model_copy(
            update={
                "ticker": ticker,
                "event_ticker": f"E-{ticker}",
                "status": MarketStatus.CLOSED.value,
                "open_time": None if opened is None else coverage_from + opened,
                "close_time": watermark + closes,
                "settlement_ts": None,
                "volume_24h_fp": Decimal(0),
                "volume_fp": Decimal(50),
            }
        )
        for ticker, _, opened, closes in TRADE_FIXTURES
    ]
    from manta_trading.data.kalshi import models as km

    series = [
        km.Series(ticker=f"E-{ticker}-SERIES", category=category, title=ticker)
        for ticker, category, _, _ in TRADE_FIXTURES
    ]
    await write_catalog(CatalogRepository(kalshi_conn), markets, series)
    repo = TradeRepository(kalshi_conn, RULE_C, trades_excluded=frozenset())
    async with repo.transaction():
        await repo.init_state(coverage_from, coverage_from)
        await repo.advance_watermark(watermark)
        await repo.set_last_full_sync(now)


async def test_trade_status_fields_and_the_four_counts_partition(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    from test_kalshi_candles import EVERYTHING, RULE_C

    from manta_trading.data.kalshi.constants import TRADE_LAG_STALE_AFTER
    from manta_trading.data.kalshi.trade_status import read_trade_status

    now = datetime.now(UTC).replace(microsecond=0)
    coverage_from = now - 30 * DAY
    watermark = now - DAY
    await _seed_trade_status(
        kalshi_conn, coverage_from=coverage_from, watermark=watermark, now=now
    )

    with psycopg.connect(kalshi_db) as conn:
        status = read_trade_status(conn, RULE_C, frozenset())
    assert status is not None
    assert status.last_phase_at == now
    assert status.tape_through == watermark
    assert status.coverage_from == coverage_from
    assert DAY <= status.lag < DAY + timedelta(minutes=5)
    assert status.behind is True and status.lag > TRADE_LAG_STALE_AFTER
    assert status.complete_through_close == 1  # COMPLETE
    assert status.partial_history == 2  # PARTIAL, OPENNULL
    assert status.short_of_close == 2  # STRADDLE (not partial), SHORT
    assert status.before_coverage == 1  # BEFORE
    # The partition: six selected closed markets (SPORTS excluded, STILLOPEN
    # open), each counted exactly once.
    assert (
        status.complete_through_close
        + status.partial_history
        + status.short_of_close
        + status.before_coverage
        == 6
    )
    payload = status.to_dict()
    assert payload["tape_through"] == watermark.isoformat()
    assert payload["coverage_from"] == coverage_from.isoformat()
    assert payload["behind"] is True
    assert payload["lag_minutes"] >= 24 * 60
    assert "cutoff" not in payload

    # The counts respect the rule: everything selected, SPORTS joins the
    # complete bucket and nothing else moves.
    with psycopg.connect(kalshi_db) as conn:
        everything = read_trade_status(conn, EVERYTHING, frozenset())
    assert everything is not None
    assert everything.complete_through_close == 2
    assert everything.partial_history == 2
    assert everything.short_of_close == 2
    assert everything.before_coverage == 1


async def test_trade_status_filter_rescopes_buckets_and_extends_the_partition(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """Slice 268, Task 5.3: with ``Politics`` filtered, every one of the six
    selected closed markets is tape-filtered — the four buckets re-scope to
    the unfiltered (zero) and the extended partition still covers the total.
    An empty filter leaves every number as the test above proves it."""
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.trade_status import read_trade_status

    now = datetime.now(UTC).replace(microsecond=0)
    await _seed_trade_status(
        kalshi_conn, coverage_from=now - 30 * DAY, watermark=now - DAY, now=now
    )
    with psycopg.connect(kalshi_db) as conn:
        filtered = read_trade_status(conn, RULE_C, frozenset({"Politics"}))
        unfiltered = read_trade_status(conn, RULE_C, frozenset())
    assert filtered is not None
    assert filtered.tape_filtered_markets == 6
    assert filtered.complete_through_close == 0
    assert filtered.partial_history == 0
    assert filtered.short_of_close == 0
    assert filtered.before_coverage == 0
    assert filtered.excluded_categories == frozenset({"Politics"})
    assert unfiltered is not None
    assert unfiltered.tape_filtered_markets == 0
    assert (
        unfiltered.complete_through_close,
        unfiltered.partial_history,
        unfiltered.short_of_close,
        unfiltered.before_coverage,
    ) == (1, 2, 2, 1)


async def test_status_command_renders_the_trades_filter(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """Slice 268, Task 5.3 (design Success Criterion 5): the filter line and
    JSON block with a filter set; ``none`` and a zeroed block when unset."""
    import json

    from typer.testing import CliRunner

    from manta_trading.cli.app import app

    now = datetime.now(UTC).replace(microsecond=0)
    await _seed_trade_status(
        kalshi_conn, coverage_from=now - 30 * DAY, watermark=now - DAY, now=now
    )
    async with CatalogRepository(kalshi_conn).transaction():
        await CatalogRepository(kalshi_conn).set_last_full_sync(Surface.CATALOG, now)
    runner = CliRunner()
    command = ["data", "kalshi", "status"]
    base_env = {"MT_TIMESCALE_DB_URL": kalshi_db}
    filter_env = {**base_env, "MT_KALSHI_TRADES_EXCLUDED_CATEGORIES": "Politics"}

    with_filter = runner.invoke(app, [*command, "--json"], env=filter_env)
    assert with_filter.exit_code == 0, with_filter.output
    payload = json.loads(with_filter.stdout)
    assert payload["trades"]["filter"] == {
        "excluded_categories": ["Politics"],
        "tape_filtered_markets": 6,
    }
    rich = runner.invoke(app, command, env=filter_env)
    text = " ".join(rich.output.split())
    assert (
        "trades filter excluding Politics (MT_KALSHI_TRADES_EXCLUDED_CATEGORIES)"
        in text
    )
    assert (
        "tape-filtered 6 closed markets "
        "(stored history kept; completeness not evaluated)" in text
    )

    unset = runner.invoke(app, [*command, "--json"], env=base_env)
    assert unset.exit_code == 0, unset.output
    payload = json.loads(unset.stdout)
    assert payload["trades"]["filter"] == {
        "excluded_categories": [],
        "tape_filtered_markets": 0,
    }
    rich = runner.invoke(app, command, env=base_env)
    text = " ".join(rich.output.split())
    assert "trades filter none (MT_KALSHI_TRADES_EXCLUDED_CATEGORIES)" in text
    assert "tape-filtered" not in text


async def test_trade_status_is_not_behind_within_the_horizon(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.constants import TRADE_LAG_STALE_AFTER
    from manta_trading.data.kalshi.trade_status import read_trade_status

    now = datetime.now(UTC).replace(microsecond=0)
    fresh = now - TRADE_LAG_STALE_AFTER + timedelta(minutes=30)
    await _seed_trade_status(
        kalshi_conn, coverage_from=now - 30 * DAY, watermark=fresh, now=now
    )
    with psycopg.connect(kalshi_db) as conn:
        status = read_trade_status(conn, RULE_C, frozenset())
    assert status is not None
    assert status.behind is False
    assert timedelta(minutes=89) <= status.lag <= timedelta(minutes=95)
    assert status.to_dict()["lag_minutes"] in range(89, 96)


# ---------------------------------------------------------------------------
# Historical line and the effective floor (slice 267, Task 7.3)
# ---------------------------------------------------------------------------


def test_historical_never_run_is_none(kalshi_db: str):
    from manta_trading.data.kalshi.historical_status import read_historical_status

    with psycopg.connect(kalshi_db) as conn:
        assert read_historical_status(conn) is None


async def test_effective_floor_moves_before_coverage_and_the_partition_holds(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """Criterion 7: with a historical watermark below the live floor,
    ``coverage_from`` is that watermark, ``BEFORE`` (closed a day below the
    live floor) leaves the before-coverage bucket, and the four still sum."""
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.constants import HISTORICAL_TRADES_FLOOR
    from manta_trading.data.kalshi.historical_status import read_historical_status
    from manta_trading.data.kalshi.trade_repository import TradeRepository
    from manta_trading.data.kalshi.trade_status import read_trade_status

    now = datetime.now(UTC).replace(microsecond=0)
    live_floor = now - 30 * DAY
    watermark = now - DAY
    await _seed_trade_status(
        kalshi_conn, coverage_from=live_floor, watermark=watermark, now=now
    )
    historical = TradeRepository(
        kalshi_conn, RULE_C, trades_excluded=frozenset(), surface=Surface.HISTORICAL
    )
    descended = live_floor - 40 * DAY
    async with historical.transaction():
        await historical.init_state(live_floor, HISTORICAL_TRADES_FLOOR)
        await historical.advance_watermark(descended)
        await historical.set_last_full_sync(now)

    with psycopg.connect(kalshi_db) as conn:
        status = read_trade_status(conn, RULE_C, frozenset())
        line = read_historical_status(conn)
    assert status is not None
    assert status.coverage_from == descended
    # BEFORE (opened live_floor - 10d) and PARTIAL (opened live_floor - 5d)
    # both opened at or after the effective floor and closed before the
    # watermark: complete now. Only OPENNULL stays partial.
    assert status.before_coverage == 0
    assert status.complete_through_close == 3
    assert status.partial_history == 1
    assert status.short_of_close == 2
    assert (
        status.complete_through_close
        + status.partial_history
        + status.short_of_close
        + status.before_coverage
        == 6
    )
    assert line is not None
    assert line.last_phase_at == now
    assert line.archive_walked is True and line.archive_in_progress is False
    assert line.tape_from == descended and line.tape_to == live_floor
    assert line.floor == HISTORICAL_TRADES_FLOOR and line.floor_reached is False
    assert set(line.to_dict()) == {
        "last_phase_at",
        "archive_walked",
        "archive_in_progress",
        "tape_from",
        "tape_to",
        "floor",
        "floor_reached",
    }


async def test_historical_line_while_the_walk_is_in_progress(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    from test_kalshi_candles import RULE_C

    from manta_trading.data.kalshi.constants import HISTORICAL_TRADES_FLOOR
    from manta_trading.data.kalshi.historical_status import read_historical_status
    from manta_trading.data.kalshi.trade_repository import TradeRepository

    historical = TradeRepository(
        kalshi_conn, RULE_C, trades_excluded=frozenset(), surface=Surface.HISTORICAL
    )
    async with historical.transaction():
        await historical.set_cursor("page-3")
    with psycopg.connect(kalshi_db) as conn:
        line = read_historical_status(conn)
    assert line is not None
    assert line.archive_in_progress is True and line.archive_walked is False
    assert line.tape_from is None and line.tape_to is None
    assert line.floor == HISTORICAL_TRADES_FLOOR and line.floor_reached is False


async def test_status_command_json_and_rich_carry_the_historical_line(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    """Slice 267, Task 7.4: ``mt data kalshi status`` against the throwaway
    database — ``historical`` is ``null`` before the phase has run and a
    mapping once its row exists; the Rich line renders in both states. The
    database URL is passed explicitly to the runner (environment beats the
    checkout's ``.env``), and the payload proves it was the seeded one."""
    import json

    from test_kalshi_candles import RULE_C
    from typer.testing import CliRunner

    from manta_trading.cli.app import app
    from manta_trading.cli.commands.kalshi_status_render import NEVER_RUN_HISTORICAL
    from manta_trading.data.kalshi.trade_repository import TradeRepository

    now = datetime.now(UTC).replace(microsecond=0)
    await _seed_trade_status(
        kalshi_conn, coverage_from=now - 30 * DAY, watermark=now - DAY, now=now
    )
    async with CatalogRepository(kalshi_conn).transaction():
        await CatalogRepository(kalshi_conn).set_last_full_sync(Surface.CATALOG, now)
    runner = CliRunner()
    env = {"MT_TIMESCALE_DB_URL": kalshi_db}
    command = ["data", "kalshi", "status"]

    before = runner.invoke(app, [*command, "--json"], env=env)
    assert before.exit_code == 0, before.output
    payload = json.loads(before.stdout)
    assert payload["synced"] is True
    assert payload["trades"]["before_coverage"] == 1  # the seeded fixture set
    assert payload["historical"] is None
    rich = runner.invoke(app, command, env=env)
    assert NEVER_RUN_HISTORICAL in rich.output

    historical = TradeRepository(
        kalshi_conn, RULE_C, trades_excluded=frozenset(), surface=Surface.HISTORICAL
    )
    async with historical.transaction():
        await historical.set_cursor("page-2")
        await historical.set_last_full_sync(now)
    after = runner.invoke(app, [*command, "--json"], env=env)
    assert after.exit_code == 0, after.output
    line = json.loads(after.stdout)["historical"]
    assert line["archive_in_progress"] is True and line["archive_walked"] is False
    assert line["tape_from"] is None and line["floor_reached"] is False
    assert line["behind_cutoff_candles_remaining"] is None  # no candles row yet
    rich = runner.invoke(app, command, env=env)
    assert "archive walk in progress" in " ".join(rich.output.split())
