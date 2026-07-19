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

from manta_trading.constants import MINUTE_OHLCV_CHUNK_INTERVAL
from manta_trading.market.maintenance.rechunk import (
    PreflightError,
    run_rechunk,
)

TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

TABLE = "scratch_166_rechunk"
CAGG = "scratch_166_rechunk_5min"

pytestmark = pytest.mark.skipif(
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
