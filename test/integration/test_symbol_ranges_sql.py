"""Integration tests: the real available-ranges statements (slice 187 D2).

Runs against a throwaway database (``ephemeral_db``), so these tests never
mutate shared state and never read production. Requires
``MT_TIMESCALE_TEST_URL``.

Unit tests cover ``merge_available_ranges`` as pure logic and mock the fetch
functions at the ``execute()`` boundary. What only a real database can answer is
whether the three statements in ``queries.py`` — including their casts, their
``UNION ALL`` shapes, and their bound types — actually produce the values the
merge is fed. That is what this module asserts, over all four D2 cases plus a
symbol present in neither cagg.

**Equivalence is asserted at date grain**, following
``test_data_status_equivalence.py``'s precedent (167 D7). The minute family's
coverage derives from the 4-hour cagg, so ``first_bucket``/``last_bucket`` are
bucket-truncated: a raw ``MIN(time)`` of 14:31 becomes a ``first_bucket`` of
12:00. A 4-hour bucket start always shares the UTC date of every bar inside it,
so the truncation is invisible at the ``::date`` grain this endpoint reports —
which is D2's byte-identical-at-date-grain claim, asserted here rather than
argued.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.api_server.queries import (
    fetch_symbol_coverage,
    fetch_symbol_head,
    fetch_universe_edges,
    merge_available_ranges,
)
from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    DAILY_COVERAGE_VIEW,
    MINUTE_COVERAGE_VIEW,
    CycleGranularity,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_MINUTE = CycleGranularity.MINUTE
_DAILY = CycleGranularity.DAILY

# The coverage horizon these fixtures are built around.
#
# **How the fixture holds coverage back, and why it is not done with the refresh
# window.** ``refresh_continuous_aggregate`` with an explicit ``[start, end]``
# materializes every bucket the range touches, so trimming the window does not
# reliably leave later data unmaterialized — verified directly against the
# engine on 2026-08-04: with a 365-day bucket, ends of ``horizon``,
# ``horizon - 1 day`` and ``horizon + 1 day`` all wrote the bucket holding the
# post-horizon rows.
#
# So the fixture reproduces the production shape the way production produced it
# (D5): seed the history, refresh coverage, *then* write bars past the horizon
# and never refresh again. That is a cagg which is not broken — it simply
# stopped being re-materialized while raw kept moving, which is the exact
# condition the head probe exists to see over. Same pattern as
# ``test_coverage_content_edge.py``.
_HORIZON = datetime(2024, 12, 27, tzinfo=UTC)

# Where the post-horizon ("after") rows are written, once coverage is already
# materialized. Comfortably past the horizon so the head probe's bound cannot
# straddle it.
_PAST_HORIZON = _HORIZON + timedelta(days=60)

# One symbol per D2 case, named for the case so a failure reads plainly.
_SPANNING = "ZZSPAN"  # data before and after the horizon
_BEFORE = "ZZBEFORE"  # delisted: everything before the horizon
_AFTER = "ZZAFTER"  # backfilled: everything after the horizon
_ABSENT = "ZZNONE"  # in instruments-land but in neither cagg nor raw

_LAZY_DAILY_SQL = """
    SELECT MIN(time AT TIME ZONE 'UTC')::date, MAX(time AT TIME ZONE 'UTC')::date
    FROM daily_ohlcv WHERE symbol = %s
"""
"""The pre-187 unbounded query, kept here **only** as the equivalence oracle.

This is the statement D1 measured at 2.5-4.0 s on prod and that the slice
removes from the read path. It is affordable against a fixture with a handful of
chunks; it must never reappear in ``queries.py``.
"""

_LAZY_MINUTE_SQL = """
    SELECT MIN(time_bucket AT TIME ZONE 'UTC')::date,
           MAX(time_bucket AT TIME ZONE 'UTC')::date
    FROM minute_5min_ohlcv WHERE symbol = %s
"""


def _daily_rows(symbol: str, start: datetime, days: int) -> list[tuple[object, ...]]:
    return [
        (start + timedelta(days=offset), symbol, 10.0, 10.0, 10.0, 10.0, 100)
        for offset in range(days)
    ]


def _minute_rows(
    symbol: str, start: datetime, count: int
) -> list[tuple[object, ...]]:
    # 14:31 UTC deliberately: a minute inside a 4-hour bucket that starts at
    # 12:00, so bucket truncation is actually exercised rather than assumed away.
    return [
        (start + timedelta(minutes=offset), symbol, 10.0, 10.0, 10.0, 10.0, 100)
        for offset in range(count)
    ]


@pytest.fixture
def ranges_db(ephemeral_db: str) -> str:
    """Ephemeral DB seeded with all four D2 cases, coverage refreshed to the
    horizon only.

    Function-scoped, matching ``ephemeral_db``. Each test therefore gets a
    genuinely fresh database rather than sharing chunk layout with the previous
    one — the isolation slice 168's precedent established, and worth the reseed.
    """
    ephemeral = ephemeral_db
    with ConnectionPool(ephemeral, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)

    # --- phase 1: the history coverage will know about ----------------------
    span_start = _HORIZON - timedelta(days=200)
    before_start = _HORIZON - timedelta(days=300)
    history_daily = [
        *_daily_rows(_SPANNING, span_start, 30),
        *_daily_rows(_BEFORE, before_start, 20),
    ]
    history_minute = [
        *_minute_rows(_SPANNING, span_start + timedelta(hours=14, minutes=31), 120),
        *_minute_rows(_BEFORE, before_start + timedelta(hours=14, minutes=31), 120),
    ]
    _insert(ephemeral, history_daily, history_minute)

    window_start = _HORIZON - 4 * COVERAGE_BUCKET_INTERVAL
    _refresh_all(ephemeral, window_start, _HORIZON)

    # --- phase 2: what coverage will never see ------------------------------
    # Written *after* the refresh and never re-materialized into coverage. This
    # is the production shape (D5), and it is what the head probe must find.
    future_daily = [
        *_daily_rows(_SPANNING, _PAST_HORIZON, 30),
        *_daily_rows(_AFTER, _PAST_HORIZON, 20),
    ]
    future_minute = [
        *_minute_rows(_SPANNING, _PAST_HORIZON + timedelta(hours=14, minutes=31), 120),
        *_minute_rows(_AFTER, _PAST_HORIZON + timedelta(hours=14, minutes=31), 120),
    ]
    _insert(ephemeral, future_daily, future_minute)

    # The 5min cagg is the minute head probe's *source*, so unlike coverage it
    # must reach the raw edge — a stale one would make the probe under-report by
    # construction rather than by defect.
    with psycopg.connect(ephemeral, autocommit=True) as conn:
        conn.execute(
            "CALL refresh_continuous_aggregate('minute_5min_ohlcv', %s, %s)",
            (window_start, _PAST_HORIZON + 10 * COVERAGE_BUCKET_INTERVAL),
        )
    return ephemeral


def _insert(
    url: str,
    daily: list[tuple[object, ...]],
    minute: list[tuple[object, ...]],
) -> None:
    """Write raw bars to both hypertables."""
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO daily_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                daily,
            )
            cur.executemany(
                "INSERT INTO minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                minute,
            )
        conn.commit()


def _refresh_all(url: str, start: datetime, end: datetime) -> None:
    """Materialize the parent caggs and then both coverage caggs.

    Parent before child: ``minute_coverage`` is hierarchical over
    ``minute_4hour_ohlcv`` (167 D3), so refreshing it first would roll up an
    unmaterialized parent.
    """
    with psycopg.connect(url, autocommit=True) as conn:
        for view in (
            "minute_5min_ohlcv",
            "minute_4hour_ohlcv",
            MINUTE_COVERAGE_VIEW,
            DAILY_COVERAGE_VIEW,
        ):
            conn.execute(
                f"CALL refresh_continuous_aggregate('{view}', %s, %s)",  # noqa: S608
                (start, end),
            )


def _merged(
    conn: psycopg.Connection[object], symbol: str
) -> dict[CycleGranularity, tuple[date, date]]:
    """The full D2 read path, exactly as ``get_symbol`` runs it."""
    edges = fetch_universe_edges(conn)
    coverage = fetch_symbol_coverage(conn, symbol)
    head = fetch_symbol_head(conn, symbol, edges)
    return merge_available_ranges(coverage, head)


def _lazy(
    conn: psycopg.Connection[object], symbol: str
) -> dict[CycleGranularity, tuple[date, date]]:
    """The pre-187 answer, at date grain, for equivalence."""
    result: dict[CycleGranularity, tuple[date, date]] = {}
    for family, sql in (
        (_MINUTE, _LAZY_MINUTE_SQL),
        (_DAILY, _LAZY_DAILY_SQL),
    ):
        row = conn.execute(sql, (symbol,)).fetchone()
        if row is not None and row[0] is not None and row[1] is not None:
            result[family] = (row[0], row[1])
    return result


class TestUniverseEdges:
    def test_both_families_have_an_edge_after_refresh(self, ranges_db: str) -> None:
        with psycopg.connect(ranges_db) as conn:
            edges = fetch_universe_edges(conn)
        assert set(edges) == {_MINUTE, _DAILY}
        assert edges[_DAILY] is not None
        assert edges[_MINUTE] is not None

    def test_edge_reflects_the_refresh_horizon_not_the_raw_edge(
        self, ranges_db: str
    ) -> None:
        # The whole premise of the head probe: coverage stops at the horizon
        # while raw runs past it. If this ever fails, the fixture no longer
        # reproduces the production shape and the tests below prove nothing.
        with psycopg.connect(ranges_db) as conn:
            edges = fetch_universe_edges(conn)
            raw_edge = conn.execute(
                "SELECT MAX(time AT TIME ZONE 'UTC')::date FROM daily_ohlcv"
            ).fetchone()
        assert raw_edge is not None
        assert edges[_DAILY] is not None
        assert edges[_DAILY] < raw_edge[0], (
            "coverage must trail raw for this fixture to exercise the head probe"
        )


class TestD2Cases:
    """The four cases, each against the lazy result as oracle (criterion 2)."""

    @pytest.mark.parametrize(
        "symbol", [_SPANNING, _BEFORE, _AFTER, _ABSENT]
    )
    def test_merged_equals_lazy_at_date_grain(
        self, ranges_db: str, symbol: str
    ) -> None:
        with psycopg.connect(ranges_db) as conn:
            merged = _merged(conn, symbol)
            lazy = _lazy(conn, symbol)
        assert merged == lazy, (
            f"{symbol}: merged {merged} != lazy {lazy} — a difference here is "
            "either a merge defect or the D3 residual window; D2 requires it be "
            "explained, not tolerated"
        )

    def test_spanning_takes_start_from_coverage_and_end_from_head(
        self, ranges_db: str
    ) -> None:
        # The common case, and the one that proves both halves of the COALESCE.
        with psycopg.connect(ranges_db) as conn:
            edges = fetch_universe_edges(conn)
            coverage = fetch_symbol_coverage(conn, _SPANNING)
            head = fetch_symbol_head(conn, _SPANNING, edges)
            merged = merge_available_ranges(coverage, head)

        assert merged[_DAILY][0] == coverage[_DAILY][0], "start must come from coverage"
        assert merged[_DAILY][1] == head[_DAILY][1], "end must come from the head probe"
        assert head[_DAILY][1] > coverage[_DAILY][1], (
            "the head probe must see past the coverage horizon, else the "
            "assertion above passes vacuously"
        )

    def test_before_horizon_only_falls_back_to_coverage_for_both_ends(
        self, ranges_db: str
    ) -> None:
        # Delisted: the head probe returns (None, None) and must not degrade to
        # a scan or an error.
        with psycopg.connect(ranges_db) as conn:
            edges = fetch_universe_edges(conn)
            head = fetch_symbol_head(conn, _BEFORE, edges)
            merged = _merged(conn, _BEFORE)

        assert head[_DAILY] == (None, None)
        assert _DAILY in merged, "coverage must supply both ends when head is empty"

    def test_after_horizon_only_comes_from_the_head_probe(
        self, ranges_db: str
    ) -> None:
        # No coverage row exists for this symbol, which is why `start` coalesces
        # from coverage *first* rather than taking it unconditionally.
        with psycopg.connect(ranges_db) as conn:
            coverage = fetch_symbol_coverage(conn, _AFTER)
            merged = _merged(conn, _AFTER)

        assert coverage.get(_DAILY, (None, None))[0] is None
        assert _DAILY in merged
        assert merged[_DAILY][0] is not None

    def test_symbol_in_neither_cagg_nor_raw_yields_no_families(
        self, ranges_db: str
    ) -> None:
        with psycopg.connect(ranges_db) as conn:
            assert _merged(conn, _ABSENT) == {}

    def test_minute_bucket_truncation_is_invisible_at_date_grain(
        self, ranges_db: str
    ) -> None:
        """D2's explicit claim: coverage timestamps are truncated to the parent
        4-hour bucket, but a bucket start shares the UTC date of every bar in
        it, so the date-grain answer is unaffected."""
        with psycopg.connect(ranges_db) as conn:
            merged = _merged(conn, _SPANNING)
            lazy = _lazy(conn, _SPANNING)
            truncated = conn.execute(
                f"SELECT MIN(first_bucket AT TIME ZONE 'UTC') "  # noqa: S608
                f"FROM {MINUTE_COVERAGE_VIEW} WHERE symbol = %s",
                (_SPANNING,),
            ).fetchone()
            exact = conn.execute(
                "SELECT MIN(time AT TIME ZONE 'UTC') FROM minute_ohlcv "
                "WHERE symbol = %s",
                (_SPANNING,),
            ).fetchone()

        assert truncated is not None and exact is not None
        assert truncated[0] < exact[0], (
            "the fixture must actually exercise truncation (bars start at 14:31, "
            "bucket at 12:00), otherwise the equality below is vacuous"
        )
        assert merged[_MINUTE] == lazy[_MINUTE]
