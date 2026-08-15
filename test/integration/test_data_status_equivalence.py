"""Integration tests: cagg-backed vs raw-scan data_status equivalence (slice 167 s7).

Runs against a throwaway database (``ephemeral_db``), so these tests never
mutate shared state. Requires ``MT_TIMESCALE_TEST_URL``.

**The equivalence definition is date-normalized (review F003 / design D7), not
literal.** A literal ``raw_row == cagg_row`` assertion is deliberately *not*
used, and would be wrong in two distinct ways:

1. ``minute_coverage`` is hierarchical over the 4-hour cagg, so its
   ``first_bucket``/``last_bucket`` are **bucket-truncated**. A raw
   ``MIN(time)`` of 14:31 becomes a cagg ``first_bucket`` of 12:00. The bars
   are all present and counted; only the reported instants shift. Asserting
   literal timestamp equality would fail on a correct implementation.
2. The trailing edge legitimately lags by up to the refresh end_offset, so
   even exact-looking values can differ near ``now()``.

What must hold *exactly* is ``bars_stored`` — a rollup that drops or
double-counts bars is a real defect, and truncation does not excuse it.

The daily branch is asserted **more strictly**: ``daily_coverage`` reads raw
``daily_ohlcv`` rather than a parent cagg, so its timestamps must match
exactly. Applying the minute-side truncation tolerance there would let a
genuine daily regression hide behind an allowance it never needed (7.1.4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from manta_trading.constants import COVERAGE_BUCKET_INTERVAL

# --------------------------------------------------------------------------
# Fixture symbols — one per coverage case required by 7.1.3.
# --------------------------------------------------------------------------

_SYM_COVERED = "ZZEQCOV"
"""Bars in both granularities; the ordinary case."""

_SYM_PARTIAL = "ZZEQPART"
"""Daily bars only. The minute branch must still yield a row with 0 bars."""

_SYM_EMPTY = "ZZEQEMPTY"
"""In `instruments`, but no bars at all. Must still appear with
`bars_stored = 0` via the LEFT JOIN + COALESCE — never as a dropped row."""

_ALL_SYMBOLS = (_SYM_COVERED, _SYM_PARTIAL, _SYM_EMPTY)

# A settled historical window: far enough from the refresh policies' trailing
# edge that a scheduled refresh cannot race the assertions. The seeded span may
# cross coverage-bucket boundaries (it does at slice 169's 7-day width); the
# rollup arithmetic sums across buckets per symbol, so that stays unambiguous.
_FIXTURE_START = datetime(2010, 3, 1, 14, 31, tzinfo=UTC)
"""Deliberately :31 past the hour — a round :00 start would make the
bucket-truncation tolerance untestable, since truncation would be a no-op."""

_MINUTE_BAR_COUNT = 240
_DAILY_BAR_COUNT = 30
_DAILY_START = datetime(2010, 3, 1, 0, 0, tzinfo=UTC)


def _apply_migrations(url: str) -> None:
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)


def _seed(url: str) -> None:
    """Seed instruments plus bars for the covered and partially-covered symbols."""
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            for symbol in _ALL_SYMBOLS:
                cur.execute(
                    "INSERT INTO instruments "
                    "(canonical_id, symbol, asset_class, venue, "
                    " trading_calendar_id, delisted_at_eodhd, "
                    " eodhd_type, eodhd_exchange) "
                    "VALUES (%s, %s, 'equity', 'US', 'NYSE', FALSE, "
                    "        'Common Stock', 'US') "
                    "ON CONFLICT (canonical_id) DO NOTHING",
                    (f"EQ:{symbol}", symbol),
                )

            minute_rows = [
                (
                    _FIXTURE_START + timedelta(minutes=i),
                    _SYM_COVERED,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    100,
                )
                for i in range(_MINUTE_BAR_COUNT)
            ]
            cur.executemany(
                "INSERT INTO minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                minute_rows,
            )

            # Daily bars for BOTH covered and partially-covered symbols: the
            # partial case is "daily present, minute absent".
            daily_rows = [
                (
                    _DAILY_START + timedelta(days=i),
                    symbol,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    100,
                )
                for symbol in (_SYM_COVERED, _SYM_PARTIAL)
                for i in range(_DAILY_BAR_COUNT)
            ]
            cur.executemany(
                "INSERT INTO daily_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                daily_rows,
            )
        conn.commit()


def _refresh_coverage(url: str) -> None:
    """Materialize both coverage caggs over the seeded historical window.

    Refresh cannot run inside a transaction block, hence autocommit. The window
    must span at least two bucket widths (TimescaleDB rejects a narrower one) —
    the same engine rule that forced the 750-day policy start_offset.

    **Order matters.** ``minute_coverage`` is hierarchical over the 4-hour cagg,
    so the parent must be materialized first; refreshing the child alone rolls
    up an empty parent and yields 0 bars. Measured on a throwaway DB: child-only
    gives 0, parent-then-child gives the full 240.
    """
    from manta_trading.constants import (
        DAILY_COVERAGE_VIEW,
        GRANULARITY_SOURCE,
        MINUTE_COVERAGE_VIEW,
        Granularity,
    )

    # The window must CONTAIN every seeded bar, and separately must span at
    # least two buckets. Deriving it from the seeded data rather than from a
    # multiple of the bucket width is the point: the earlier
    # `_DAILY_START - 4*bucket .. _FIXTURE_START + 4*bucket` form happened to
    # swallow the fixture only because a 365-day bucket made the padding
    # enormous. At slice 169's 7-day width that window ended before the last
    # two daily bars, and the equivalence assertions failed with "cagg 28 !=
    # raw 30" — a fixture defect that reads exactly like a real
    # under-materialization bug.
    last_seeded = max(
        _DAILY_START + timedelta(days=_DAILY_BAR_COUNT),
        _FIXTURE_START + timedelta(minutes=_MINUTE_BAR_COUNT),
    )
    pad = max(COVERAGE_BUCKET_INTERVAL * 2, timedelta(days=1))
    window_start = min(_DAILY_START, _FIXTURE_START) - pad
    window_end = last_seeded + pad

    with psycopg.connect(url, autocommit=True) as conn:
        for view in (
            GRANULARITY_SOURCE[Granularity.H4],
            MINUTE_COVERAGE_VIEW,
            DAILY_COVERAGE_VIEW,
        ):
            conn.execute(
                f"CALL refresh_continuous_aggregate('{view}', %s, %s)",
                (window_start, window_end),
            )


def _unescape(sql: str) -> str:
    """Undo the doubled single quotes used for DO-block embedding.

    The builder emits SQL destined for a PL/pgSQL EXECUTE literal; running it
    directly requires the original quoting.
    """
    return sql.replace("''", "'")


def _install_variant(url: str, *, cagg_backed: bool) -> None:
    from manta_trading.market.schema.migrations.minute import (
        _build_data_status_view_sql,
    )

    sql = _build_data_status_view_sql(
        include_daily_branch=True,
        include_trading_sessions_cte=True,
        cagg_backed_bars_summary=cagg_backed,
    )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_unescape(sql))


def _read_status(url: str) -> dict[tuple[str, str], dict]:
    """Return {(symbol, granularity): row} for the fixture symbols."""
    with psycopg.connect(url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT symbol, granularity, first_bar_ts, last_bar_ts, "
                "       bars_stored, health, gap_count "
                "FROM data_status WHERE symbol = ANY(%s)",
                (list(_ALL_SYMBOLS),),
            )
            return {(r["symbol"], r["granularity"]): r for r in cur.fetchall()}


@pytest.fixture()
def both_variants(ephemeral_db: str):
    """Seed once, then read data_status under each variant of the same DB.

    Building both against one seeded database is what makes this an equivalence
    test rather than two independent snapshots (7.1.1).
    """
    _apply_migrations(ephemeral_db)
    _seed(ephemeral_db)
    _refresh_coverage(ephemeral_db)

    _install_variant(ephemeral_db, cagg_backed=False)
    raw = _read_status(ephemeral_db)

    _install_variant(ephemeral_db, cagg_backed=True)
    cagg = _read_status(ephemeral_db)

    return raw, cagg, ephemeral_db


# --------------------------------------------------------------------------
# 7.1 — date-normalized equivalence
# --------------------------------------------------------------------------


def test_same_row_keys_under_both_variants(both_variants) -> None:
    """No symbol/granularity pair may appear or vanish when the source changes."""
    raw, cagg, _ = both_variants
    assert set(raw) == set(cagg)
    # Every symbol appears at both granularities, including the empty one.
    for symbol in _ALL_SYMBOLS:
        assert (symbol, "daily") in cagg
        assert (symbol, "minute") in cagg


def test_bars_stored_exactly_equal(both_variants) -> None:
    """Counts must match exactly — truncation shifts instants, never totals."""
    raw, cagg, _ = both_variants
    for key, raw_row in raw.items():
        assert cagg[key]["bars_stored"] == raw_row["bars_stored"], (
            f"{key}: cagg {cagg[key]['bars_stored']} != raw {raw_row['bars_stored']}"
        )


def test_seeded_counts_are_what_we_inserted(both_variants) -> None:
    """Guards against both variants agreeing on a wrong number."""
    _, cagg, _ = both_variants
    assert cagg[(_SYM_COVERED, "minute")]["bars_stored"] == _MINUTE_BAR_COUNT
    assert cagg[(_SYM_COVERED, "daily")]["bars_stored"] == _DAILY_BAR_COUNT
    assert cagg[(_SYM_PARTIAL, "daily")]["bars_stored"] == _DAILY_BAR_COUNT


def test_dates_equal_under_normalization(both_variants) -> None:
    """F003/D7: compare `date(ts)`, not the instant."""
    raw, cagg, _ = both_variants
    for key, raw_row in raw.items():
        if raw_row["first_bar_ts"] is None:
            assert cagg[key]["first_bar_ts"] is None
            continue
        assert cagg[key]["first_bar_ts"].date() == raw_row["first_bar_ts"].date()
        assert cagg[key]["last_bar_ts"].date() == raw_row["last_bar_ts"].date()


def test_minute_timestamp_delta_within_bucket_tolerance(both_variants) -> None:
    """Minute coverage is bucket-truncated, so allow < 4 h — and no more."""
    raw, cagg, _ = both_variants
    key = (_SYM_COVERED, "minute")
    for column in ("first_bar_ts", "last_bar_ts"):
        delta = abs(cagg[key][column] - raw[key][column])
        assert delta < timedelta(hours=4), f"{column} drifted {delta}"


def test_minute_truncation_is_actually_exercised(both_variants) -> None:
    """The tolerance must be load-bearing, not vacuous.

    The fixture starts at :31 past the hour precisely so truncation moves the
    reported instant. If this ever passes with a zero delta, the fixture has
    drifted onto a bucket boundary and the tolerance test above proves nothing.
    """
    raw, cagg, _ = both_variants
    key = (_SYM_COVERED, "minute")
    assert cagg[key]["first_bar_ts"] != raw[key]["first_bar_ts"], (
        "expected bucket truncation to shift first_bar_ts; fixture may have "
        "landed on a bucket boundary"
    )
    assert cagg[key]["first_bar_ts"] < raw[key]["first_bar_ts"], (
        "truncation must move the instant backwards, never forwards"
    )


def test_daily_timestamps_exact_not_merely_same_date(both_variants) -> None:
    """7.1.4: daily reads raw, so it gets no truncation allowance."""
    raw, cagg, _ = both_variants
    for symbol in (_SYM_COVERED, _SYM_PARTIAL):
        key = (symbol, "daily")
        assert cagg[key]["first_bar_ts"] == raw[key]["first_bar_ts"]
        assert cagg[key]["last_bar_ts"] == raw[key]["last_bar_ts"]


def test_empty_symbol_yields_zero_not_a_dropped_row(both_variants) -> None:
    """7.1.3: LEFT JOIN + COALESCE must survive the source swap."""
    raw, cagg, _ = both_variants
    for granularity in ("daily", "minute"):
        key = (_SYM_EMPTY, granularity)
        assert key in cagg, f"{key} was dropped by the cagg-backed view"
        assert cagg[key]["bars_stored"] == 0
        assert cagg[key]["first_bar_ts"] is None
        assert cagg[key]["bars_stored"] == raw[key]["bars_stored"]


def test_partially_covered_symbol_zero_on_missing_granularity(
    both_variants,
) -> None:
    """Daily present, minute absent — the minute row must still exist at 0."""
    raw, cagg, _ = both_variants
    key = (_SYM_PARTIAL, "minute")
    assert key in cagg
    assert cagg[key]["bars_stored"] == 0
    assert cagg[key]["bars_stored"] == raw[key]["bars_stored"]
    assert cagg[(_SYM_PARTIAL, "daily")]["bars_stored"] == _DAILY_BAR_COUNT


def test_health_and_gap_columns_unaffected_by_source_swap(both_variants) -> None:
    """Only bars_summary changed; the rest of the view must be untouched (D2)."""
    raw, cagg, _ = both_variants
    for key, raw_row in raw.items():
        assert cagg[key]["gap_count"] == raw_row["gap_count"]
        assert cagg[key]["health"] == raw_row["health"]


# --------------------------------------------------------------------------
# 7.2 — trailing-edge lag honesty
# --------------------------------------------------------------------------


def _insert_recent_minute_bars(url: str, symbol: str, count: int) -> datetime:
    """Insert `count` minute bars ending now; return the newest bar's time."""
    now = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    rows = [
        (now - timedelta(minutes=offset), symbol, 10.0, 10.0, 10.0, 10.0, 100)
        for offset in range(count)
    ]
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                rows,
            )
        conn.commit()
    return now


def test_unrefreshed_bars_understate_then_converge(both_variants) -> None:
    """7.2: coverage understates before a refresh, and converges after one.

    This is the honesty property the whole slice rests on: the cagg is allowed
    to lag, but only downwards (never inventing bars), and a refresh must close
    the gap exactly.
    """
    _, cagg_before, url = both_variants
    baseline = cagg_before[(_SYM_COVERED, "minute")]["bars_stored"]

    new_bars = 60
    _insert_recent_minute_bars(url, _SYM_COVERED, new_bars)

    # Before any refresh: the new raw bars are invisible to coverage.
    stale_read = _read_status(url)[(_SYM_COVERED, "minute")]
    assert stale_read["bars_stored"] == baseline, (
        "unrefreshed coverage must not see new raw bars"
    )

    # Raw truth has moved; confirm the understatement is real, not a no-op seed.
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM minute_ohlcv WHERE symbol = %s",
                (_SYM_COVERED,),
            )
            raw_total = cur.fetchone()[0]
    assert raw_total == baseline + new_bars
    assert stale_read["bars_stored"] < raw_total, "coverage must understate, not equal"

    # After a refresh covering the new window, coverage converges to raw truth.
    _refresh_recent(url)
    converged = _read_status(url)[(_SYM_COVERED, "minute")]
    assert converged["bars_stored"] == raw_total, (
        f"after refresh coverage {converged['bars_stored']} != raw {raw_total}"
    )


def _refresh_recent(url: str) -> None:
    """Refresh both hops over a window wide enough to include `now`.

    The minute coverage cagg is hierarchical, so its parent (the 4-hour cagg)
    must be refreshed first — refreshing only the child would roll up stale
    parent data and the convergence assertion would fail for the wrong reason.
    """
    from manta_trading.constants import (
        GRANULARITY_SOURCE,
        MINUTE_COVERAGE_VIEW,
        Granularity,
    )

    span = COVERAGE_BUCKET_INTERVAL * 4
    window_start = datetime.now(tz=UTC) - span
    window_end = datetime.now(tz=UTC) + span

    parent_view = GRANULARITY_SOURCE[Granularity.H4]
    with psycopg.connect(url, autocommit=True) as conn:
        for view in (parent_view, MINUTE_COVERAGE_VIEW):
            conn.execute(
                f"CALL refresh_continuous_aggregate('{view}', %s, %s)",
                (window_start, window_end),
            )
