"""Integration tests: slice 166 rechunk driver on a scratch hypertable.

Requires MT_TIMESCALE_DB_URL pointing at a TimescaleDB instance. Each test
builds a gap-faithful scratch hypertable (market-hours-only data, 4-hour
chunks, compressed, optionally one attached cagg with a refresh policy) and
exercises the full Option D cycle. **Never touches minute_ohlcv** — the
driver's table/cagg parameters are its test seams.

Covers task C2's assertions plus the code-review F001 writer-lock guarantee:
  - --dry-run mutates nothing
  - a real run reduces chunk count with zero data loss, cagg preserved
  - re-running is a no-op (idempotent)
  - an interrupted run leaves a valid partial state; the next run finishes
  - pre-flight refuses while a family job is still scheduled
  - a concurrent writer is blocked while a window transaction is open

Every test is independent (function-scoped fixture rebuilds the scratch
state) — no reliance on definition order or shared mutation.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from manta_trading.constants import (
    DAILY_OHLCV_CHUNK_INTERVAL,
    MINUTE_OHLCV_CHUNK_INTERVAL,
)
from manta_trading.market.maintenance.rechunk import (
    PreflightError,
    RechunkTarget,
    run_rechunk,
)

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

TABLE = "scratch_166_rechunk"
CAGG = "scratch_166_rechunk_5min"

# Daily-shaped scratch state (slice 170 B8). Separate names so the two
# suites cannot interfere, and so neither can be confused for real data.
DAILY_TABLE = "scratch_170_rechunk_daily"
DAILY_CAGG = "scratch_170_rechunk_daily_weekly"

#: Applied per-class rather than module-wide: the 166 suite below needs the
#: working database, while the 170 daily suite builds its state inside an
#: ``ephemeral_db`` throwaway. A module-level pytestmark would skip the daily
#: suite whenever ``test/conftest.py``'s prod-safety guard scrubbed the
#: working URL — that is, by default.
requires_working_url = pytest.mark.skipif(
    not TIMESCALE_URL, reason="MT_TIMESCALE_DB_URL not set"
)


def _drop_scratch(conn: psycopg.Connection) -> None:
    conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {CAGG} CASCADE")
    conn.execute(f"DROP TABLE IF EXISTS {TABLE} CASCADE")


def _build_scratch(conn: psycopg.Connection, *, with_cagg: bool) -> None:
    """Create the gap-faithful scratch hypertable in a known pre-rechunk state.

    Two weeks of weekday-only 13:30-21:00 UTC minute bars (overnight/weekend
    chunk ranges stay empty, mirroring prod), 4-hour chunks, compression
    settings mirroring slice 160, everything before the trailing day
    compressed (the trailing day exercises SKIP_UNCOMPRESSED), and the
    target interval already set (mirrors the C3 migration-043 dependency).
    2025-01-06 is a Monday.
    """
    _drop_scratch(conn)
    conn.execute(
        f"CREATE TABLE {TABLE} ("
        "  time timestamptz NOT NULL, symbol text NOT NULL, "
        "  close numeric(12,4) NOT NULL, volume bigint NOT NULL)"
    )
    conn.execute(
        f"SELECT create_hypertable('{TABLE}', 'time', "
        "chunk_time_interval => INTERVAL '4 hours')"
    )
    conn.execute(
        f"INSERT INTO {TABLE} (time, symbol, close, volume) "
        "SELECT ts, sym, 100 + mod(extract(epoch FROM ts)::bigint, 89) * 0.01, "
        "       1000 + mod(extract(epoch FROM ts)::bigint, 997) "
        "FROM generate_series('2025-01-06 13:30+00'::timestamptz, "
        "                     '2025-01-17 20:59+00'::timestamptz, "
        "                     INTERVAL '1 minute') AS ts, "
        "     unnest(ARRAY['AAA','BBB']) AS sym "
        "WHERE extract(isodow FROM ts AT TIME ZONE 'UTC') < 6 "
        "  AND (ts AT TIME ZONE 'UTC')::time >= '13:30' "
        "  AND (ts AT TIME ZONE 'UTC')::time < '21:00'"
    )
    conn.execute(
        f"ALTER TABLE {TABLE} SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'symbol', "
        "timescaledb.compress_orderby = 'time DESC')"
    )
    conn.execute(
        f"SELECT compress_chunk(c) FROM show_chunks('{TABLE}', "
        "older_than => '2025-01-17 00:00+00'::timestamptz) AS c"
    )
    if with_cagg:
        conn.execute(
            f"CREATE MATERIALIZED VIEW {CAGG} WITH (timescaledb.continuous) AS "
            f"SELECT time_bucket('5 minutes', time) AS bucket, symbol, "
            f"       sum(volume) AS volume FROM {TABLE} GROUP BY bucket, symbol "
            "WITH NO DATA"
        )
        conn.execute(f"CALL refresh_continuous_aggregate('{CAGG}', NULL, NULL)")
        conn.execute(
            f"SELECT add_continuous_aggregate_policy('{CAGG}', "
            "start_offset => INTERVAL '30 days', "
            "end_offset => INTERVAL '5 minutes', "
            "schedule_interval => INTERVAL '5 minutes')"
        )
    conn.execute(
        f"SELECT set_chunk_time_interval('{TABLE}', "
        f"INTERVAL '{int(MINUTE_OHLCV_CHUNK_INTERVAL.total_seconds())} seconds')"
    )


def _drop_daily_scratch(conn: psycopg.Connection) -> None:
    conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {DAILY_CAGG} CASCADE")
    conn.execute(f"DROP TABLE IF EXISTS {DAILY_TABLE} CASCADE")


def _build_daily_scratch(conn: psycopg.Connection, *, with_cagg: bool) -> None:
    """Create a daily-shaped scratch hypertable in the pre-170 state.

    Mirrors prod's daily_ohlcv shape rather than the minute one: 7-day chunks
    (the interval slice 143 set) over a span covering two full 70-day grid
    windows plus a trailing partial, weekday-only rows so the empty weekend
    ranges are faithful — that emptiness is exactly why merge_chunks cannot be
    used and Option D exists.

    The grid boundaries used here were read back from TimescaleDB rather than
    computed: chunk slices land on 2024-08-14, 2024-10-23 and 2025-01-01, so
    the span below covers two whole 70-day windows plus a trailing partial.
    Each boundary is also a 7-day boundary (70 = 10 x 7), which is the nesting
    property the rewrite depends on — every 7-day chunk falls wholly inside
    one window.

    Everything before the 2025-01-01 boundary is compressed, so both closed
    windows are eligible for rewrite; the trailing partial window stays
    uncompressed to exercise SKIP_UNCOMPRESSED. The target interval is set
    last, mirroring the C2 migration-050 dependency.
    """
    _drop_daily_scratch(conn)
    conn.execute(
        f"CREATE TABLE {DAILY_TABLE} ("
        "  time timestamptz NOT NULL, symbol text NOT NULL, "
        "  close numeric(12,4) NOT NULL, volume bigint NOT NULL)"
    )
    conn.execute(
        f"SELECT create_hypertable('{DAILY_TABLE}', 'time', "
        "chunk_time_interval => INTERVAL '7 days')"
    )
    # One bar per weekday at 21:00 UTC (a daily close), two symbols.
    conn.execute(
        f"INSERT INTO {DAILY_TABLE} (time, symbol, close, volume) "
        "SELECT ts, sym, 100 + mod(extract(epoch FROM ts)::bigint, 89) * 0.01, "
        "       1000 + mod(extract(epoch FROM ts)::bigint, 997) "
        "FROM generate_series('2024-08-15 21:00+00'::timestamptz, "
        "                     '2025-01-08 21:00+00'::timestamptz, "
        "                     INTERVAL '1 day') AS ts, "
        "     unnest(ARRAY['AAA','BBB']) AS sym "
        "WHERE extract(isodow FROM ts AT TIME ZONE 'UTC') < 6"
    )
    conn.execute(
        f"ALTER TABLE {DAILY_TABLE} SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'symbol', "
        "timescaledb.compress_orderby = 'time DESC')"
    )
    # Compress everything up to the 2025-01-01 grid boundary so both closed
    # 70-day windows qualify for rewrite; a window holding even one
    # uncompressed chunk classifies SKIP_UNCOMPRESSED and is left alone.
    #
    # ``older_than`` selects chunks whose range_end is strictly BEFORE the
    # cutoff (verified empirically, not assumed): a cutoff of exactly
    # 2025-01-01 excludes the 2024-12-25..2025-01-01 chunk and silently leaves
    # the second window uncompressed. Hence a cutoff past that boundary.
    conn.execute(
        f"SELECT compress_chunk(c) FROM show_chunks('{DAILY_TABLE}', "
        "older_than => '2025-01-02 00:00+00'::timestamptz) AS c"
    )
    if with_cagg:
        conn.execute(
            f"CREATE MATERIALIZED VIEW {DAILY_CAGG} "
            "WITH (timescaledb.continuous) AS "
            f"SELECT time_bucket('7 days', time) AS bucket, symbol, "
            f"       sum(volume) AS volume FROM {DAILY_TABLE} "
            "GROUP BY bucket, symbol WITH NO DATA"
        )
        conn.execute(
            f"CALL refresh_continuous_aggregate('{DAILY_CAGG}', NULL, NULL)"
        )
        conn.execute(
            f"SELECT add_continuous_aggregate_policy('{DAILY_CAGG}', "
            "start_offset => INTERVAL '365 days', "
            "end_offset => INTERVAL '1 day', "
            "schedule_interval => INTERVAL '1 hour')"
        )
    conn.execute(
        f"SELECT set_chunk_time_interval('{DAILY_TABLE}', "
        f"INTERVAL '{int(DAILY_OHLCV_CHUNK_INTERVAL.total_seconds())} seconds')"
    )


@pytest.fixture
def daily_scratch_db(ephemeral_db: str):
    """Fresh daily-shaped scratch hypertable + cagg in a throwaway database.

    Unlike the 166 suite above (which predates the guard and runs against the
    working database), this one builds its state inside the UUID-named
    database ``ephemeral_db`` mints and drops. That keeps it runnable by
    default without opting out of ``test/conftest.py``'s prod-URL scrub, and
    means nothing here can reach real ``daily_ohlcv`` even by mistake.
    """
    with psycopg.connect(ephemeral_db, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        _build_daily_scratch(conn, with_cagg=True)
    # Teardown is the database drop itself; no explicit cleanup needed.
    return ephemeral_db


@pytest.fixture
def scratch_db():
    """Fresh scratch hypertable + cagg per test; torn down after."""
    with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
        _build_scratch(conn, with_cagg=True)
    yield
    with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
        _drop_scratch(conn)


@pytest.fixture
def scratch_db_nocagg():
    """Fresh scratch hypertable without a cagg (no policy job) per test."""
    with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
        _build_scratch(conn, with_cagg=False)
    yield
    with psycopg.connect(TIMESCALE_URL, autocommit=True) as conn:
        _drop_scratch(conn)


def _exec(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        return cur.fetchall()


def _chunk_count(conn: psycopg.Connection) -> int:
    return _exec(
        conn,
        "SELECT count(*) FROM timescaledb_information.chunks "
        "WHERE hypertable_name = %s",
        (TABLE,),
    )[0][0]


def _integrity(conn: psycopg.Connection) -> tuple:
    return _exec(
        conn,
        f"SELECT count(*), sum(volume), min(time), max(time) FROM {TABLE}",  # noqa: S608 — fixed test table name
    )[0]


def _cagg_job_id(conn: psycopg.Connection) -> int:
    return _exec(
        conn,
        "SELECT job_id FROM timescaledb_information.jobs "
        "WHERE proc_name = 'policy_refresh_continuous_aggregate' "
        "  AND hypertable_name = %s",
        (CAGG,),
    )[0][0]


def _set_job_scheduled(conn: psycopg.Connection, scheduled: bool) -> None:
    _exec(
        conn,
        "SELECT alter_job(%s, scheduled => %s)",
        (_cagg_job_id(conn), scheduled),
    )
    conn.commit()


def _run(dry_run: bool = False, max_windows: int | None = None, **kw):
    return run_rechunk(
        TIMESCALE_URL,
        dry_run=dry_run,
        table=TABLE,
        cagg_views=(CAGG,),
        max_windows=max_windows,
        **kw,
    )


@requires_working_url
class TestRechunkDriver:
    def test_preflight_refuses_scheduled_job(self, scratch_db) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            before = _chunk_count(conn)
        with pytest.raises(PreflightError, match="still scheduled"):
            _run()
        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _chunk_count(conn) == before, "refused run must not mutate"

    def test_dry_run_mutates_nothing(self, scratch_db) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            before_chunks = _chunk_count(conn)
            before_integrity = _integrity(conn)
        # Dry run is allowed even while jobs are scheduled (read-only).
        result = _run(dry_run=True)
        assert result.dry_run
        assert result.total_windows > 0
        assert result.rewritten == 0 and result.compressed_only == 0
        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _chunk_count(conn) == before_chunks
            assert _integrity(conn) == before_integrity

    def test_real_run_reduces_chunks_and_preserves_data(self, scratch_db) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            before_chunks = _chunk_count(conn)
            before_integrity = _integrity(conn)
            cagg_before = _exec(conn, f"SELECT count(*), sum(volume) FROM {CAGG}")  # noqa: S608
            _set_job_scheduled(conn, False)
        result = _run()
        assert result.rewritten > 0
        assert result.skipped_uncompressed >= 1, "trailing window skipped"
        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _chunk_count(conn) < before_chunks
            assert _integrity(conn) == before_integrity, "no data loss"
            assert (
                _exec(conn, f"SELECT count(*), sum(volume) FROM {CAGG}")  # noqa: S608
                == cagg_before
            )
            uncompressed_rewritten = _exec(
                conn,
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name = %s AND NOT is_compressed "
                "  AND range_end <= '2025-01-16 00:00+00'",
                (TABLE,),
            )[0][0]
            assert uncompressed_rewritten == 0

    def test_rerun_is_noop(self, scratch_db) -> None:
        with psycopg.connect(TIMESCALE_URL) as conn:
            _set_job_scheduled(conn, False)
        first = _run()
        assert first.rewritten > 0
        with psycopg.connect(TIMESCALE_URL) as conn:
            after_first = _chunk_count(conn)
        second = _run()
        assert second.rewritten == 0
        assert second.compressed_only == 0
        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _chunk_count(conn) == after_first

    def test_interrupted_run_resumes(self, scratch_db_nocagg) -> None:
        """A run stopped mid-way leaves a valid state the next run finishes."""
        with psycopg.connect(TIMESCALE_URL) as conn:
            before_chunks = _chunk_count(conn)
            before_integrity = _integrity(conn)

        partial = run_rechunk(
            TIMESCALE_URL, table=TABLE, cagg_views=(), max_windows=1
        )
        assert partial.rewritten == 1

        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _chunk_count(conn) < before_chunks, "one window rewritten"
            assert _integrity(conn) == before_integrity, "partial state valid"

        finish = run_rechunk(TIMESCALE_URL, table=TABLE, cagg_views=())
        assert finish.rewritten >= 1

        with psycopg.connect(TIMESCALE_URL) as conn:
            assert _integrity(conn) == before_integrity
        final = run_rechunk(
            TIMESCALE_URL, table=TABLE, cagg_views=(), dry_run=True
        )
        assert (
            final.total_windows == final.already_done + final.skipped_uncompressed
        )

    def test_concurrent_writer_blocked_during_window(
        self, scratch_db_nocagg
    ) -> None:
        """Code-review F001: the per-window EXCLUSIVE lock must block writers.

        The after_stage seam fires inside the window transaction, between the
        stage snapshot and drop_chunks — exactly the race that would silently
        destroy a concurrent writer's rows. A writer with a short lock_timeout
        must fail to commit during that span.
        """
        blocked: list[str] = []

        def try_concurrent_insert(window) -> None:
            with psycopg.connect(TIMESCALE_URL, autocommit=True) as wconn:
                wconn.execute("SET lock_timeout = '500ms'")
                try:
                    wconn.execute(
                        f"INSERT INTO {TABLE} (time, symbol, close, volume) "  # noqa: S608
                        "VALUES (%s, 'AAA', 1, 1)",
                        (window.start,),
                    )
                    pytest.fail("concurrent INSERT succeeded mid-window")
                except psycopg.errors.LockNotAvailable:
                    # Expected: EXCLUSIVE lock on the hypertable blocks writers.
                    blocked.append(str(window.start))

        run_rechunk(
            TIMESCALE_URL,
            table=TABLE,
            cagg_views=(),
            max_windows=1,
            after_stage=try_concurrent_insert,
        )
        assert blocked, "concurrent writer was NOT blocked mid-window"


# ---------------------------------------------------------------------------
# Daily target (slice 170 B8) — same driver, daily-shaped scratch table.
# Never touches daily_ohlcv or minute_ohlcv: table/cagg_views are the seams.
# ---------------------------------------------------------------------------


def _daily_chunk_count(conn: psycopg.Connection) -> int:
    return _exec(
        conn,
        "SELECT count(*) FROM timescaledb_information.chunks "
        "WHERE hypertable_name = %s",
        (DAILY_TABLE,),
    )[0][0]


def _daily_integrity(conn: psycopg.Connection) -> tuple:
    return _exec(
        conn,
        f"SELECT count(*), sum(volume), min(time), max(time) FROM {DAILY_TABLE}",  # noqa: S608 — fixed test table name
    )[0]


def _daily_cagg_totals(conn: psycopg.Connection) -> tuple:
    return _exec(conn, f"SELECT count(*), sum(volume) FROM {DAILY_CAGG}")[0]  # noqa: S608


def _set_daily_job_scheduled(conn: psycopg.Connection, scheduled: bool) -> None:
    job_id = _exec(
        conn,
        "SELECT job_id FROM timescaledb_information.jobs "
        "WHERE proc_name = 'policy_refresh_continuous_aggregate' "
        "  AND hypertable_name = %s",
        (DAILY_CAGG,),
    )[0][0]
    _exec(conn, "SELECT alter_job(%s, scheduled => %s)", (job_id, scheduled))
    conn.commit()


def _run_daily(url: str, dry_run: bool = False, **kw):
    """Drive the DAILY target against the scratch table.

    ``target`` selects the 70-day interval from the registry; ``table`` and
    ``cagg_views`` redirect it away from the real daily_ohlcv family, so this
    can never touch a production hypertable even if pointed at one.
    """
    return run_rechunk(
        url,
        dry_run=dry_run,
        target=RechunkTarget.DAILY,
        table=DAILY_TABLE,
        cagg_views=(DAILY_CAGG,),
        **kw,
    )


class TestRechunkDriverDailyTarget:
    def test_scratch_starts_over_chunked(self, daily_scratch_db) -> None:
        """Precondition: the fixture reproduces the pathology being fixed."""
        with psycopg.connect(daily_scratch_db) as conn:
            assert _daily_chunk_count(conn) > 15, (
                "scratch table should start with many 7-day chunks"
            )

    def test_preflight_refuses_scheduled_cagg_job(self, daily_scratch_db) -> None:
        """A refresh firing mid-rewrite silently loses materialized rows
        (166 A5-Q3), so this refusal is correctness-critical."""
        with psycopg.connect(daily_scratch_db) as conn:
            before = _daily_chunk_count(conn)
        with pytest.raises(PreflightError, match="still scheduled"):
            _run_daily(daily_scratch_db)
        with psycopg.connect(daily_scratch_db) as conn:
            assert _daily_chunk_count(conn) == before, "refused run must not mutate"

    def test_dry_run_mutates_nothing(self, daily_scratch_db) -> None:
        with psycopg.connect(daily_scratch_db) as conn:
            before_chunks = _daily_chunk_count(conn)
            before_integrity = _daily_integrity(conn)
        result = _run_daily(daily_scratch_db, dry_run=True)
        assert result.dry_run
        assert result.total_windows > 0
        assert result.rewritten == 0 and result.compressed_only == 0
        with psycopg.connect(daily_scratch_db) as conn:
            assert _daily_chunk_count(conn) == before_chunks
            assert _daily_integrity(conn) == before_integrity

    def test_real_run_collapses_each_window_to_one_chunk(
        self, daily_scratch_db
    ) -> None:
        """The nesting property end to end: every rewritten 70-day window
        holds exactly one chunk afterwards, with no row loss and the attached
        cagg's contents unchanged."""
        with psycopg.connect(daily_scratch_db) as conn:
            before_chunks = _daily_chunk_count(conn)
            before_integrity = _daily_integrity(conn)
            cagg_before = _daily_cagg_totals(conn)
            _set_daily_job_scheduled(conn, False)

        result = _run_daily(daily_scratch_db)
        assert result.rewritten > 0
        assert result.skipped_uncompressed >= 1, "trailing window skipped"

        with psycopg.connect(daily_scratch_db) as conn:
            assert _daily_chunk_count(conn) < before_chunks
            assert _daily_integrity(conn) == before_integrity, "no data loss"
            assert _daily_cagg_totals(conn) == cagg_before, "cagg contents changed"

            # Each closed 70-day grid window collapsed to exactly one chunk.
            per_window = _exec(
                conn,
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name = %s AND is_compressed "
                # Exclude the trailing SKIP_UNCOMPRESSED window, which keeps
                # its 7-day chunks until a later run compresses them.
                "  AND range_end <= '2025-01-01 00:00+00'::timestamptz "
                "GROUP BY (extract(epoch FROM range_start)::bigint / %s) "
                "HAVING count(*) > 1",
                (DAILY_TABLE, int(DAILY_OHLCV_CHUNK_INTERVAL.total_seconds())),
            )
            assert per_window == [], (
                f"a rewritten 70-day window holds more than one chunk: {per_window}"
            )

    def test_rewritten_chunks_span_the_full_window(self, daily_scratch_db) -> None:
        """A rewritten chunk's slice must be the whole 70-day window, not the
        7-day slice it inherited — otherwise the chunk count drops while the
        planner still sees a fine-grained grid."""
        with psycopg.connect(daily_scratch_db) as conn:
            _set_daily_job_scheduled(conn, False)
        _run_daily(daily_scratch_db)
        with psycopg.connect(daily_scratch_db) as conn:
            widths = _exec(
                conn,
                "SELECT DISTINCT range_end - range_start "
                "FROM timescaledb_information.chunks "
                "WHERE hypertable_name = %s AND is_compressed "
                # Chunks in the trailing SKIP_UNCOMPRESSED window keep their
                # 7-day slices by design; only rewritten windows are asserted.
                "  AND range_end <= '2025-01-01 00:00+00'::timestamptz",
                (DAILY_TABLE,),
            )
        assert widths == [(DAILY_OHLCV_CHUNK_INTERVAL,)], (
            f"expected every rewritten chunk to span 70 days; got {widths}"
        )

    def test_rerun_is_noop(self, daily_scratch_db) -> None:
        with psycopg.connect(daily_scratch_db) as conn:
            _set_daily_job_scheduled(conn, False)
        first = _run_daily(daily_scratch_db)
        assert first.rewritten > 0
        with psycopg.connect(daily_scratch_db) as conn:
            after_first = _daily_chunk_count(conn)
            integrity_after_first = _daily_integrity(conn)

        second = _run_daily(daily_scratch_db)
        assert second.rewritten == 0
        assert second.compressed_only == 0
        with psycopg.connect(daily_scratch_db) as conn:
            assert _daily_chunk_count(conn) == after_first
            assert _daily_integrity(conn) == integrity_after_first

    def test_preflight_refuses_wrong_dimension_interval(
        self, daily_scratch_db
    ) -> None:
        """With the interval reverted, the pre-flight must name migration 050
        — the operator's next action is to apply it."""
        with psycopg.connect(daily_scratch_db, autocommit=True) as conn:
            _set_daily_job_scheduled(conn, False)
            conn.execute(
                f"SELECT set_chunk_time_interval('{DAILY_TABLE}', "
                "INTERVAL '7 days')"
            )
        with pytest.raises(PreflightError, match="050"):
            _run_daily(daily_scratch_db)
