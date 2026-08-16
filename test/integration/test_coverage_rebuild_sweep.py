"""Integration tests: the coverage-cagg rebuild sweep (slice 169 Task G).

The unit tier covers window planning and the pre-flight refusals in isolation.
This covers the part that actually touches a database — and that must be right
before the sweep is pointed at production:

- a rebuild from empty materializes the full seeded span, not just the head;
- it is **resumable**: a second run skips what the first materialized;
- ``--force`` re-materializes anyway;
- the pre-flight refusals fire against a real job catalog, not a stub;
- ``verify_coverage`` reports content parity against the source, and detects a
  deliberately half-materialized cagg (catalog presence proves nothing — the
  2026-08-04 incident's lesson, and what slice 170's exit refresh found about
  the daily rollups).

Runs against a throwaway database created by ``ephemeral_db``. Requires
``MT_TIMESCALE_TEST_URL``. Never touches production or a shared database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest
from psycopg.rows import dict_row

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    DAILY_COVERAGE_VIEW,
    GRANULARITY_SOURCE,
    MINUTE_COVERAGE_VIEW,
    Granularity,
)
from manta_trading.market.maintenance.coverage_rebuild import (
    CoverageFamily,
    rebuild_coverage,
    verify_coverage,
)
from manta_trading.market.maintenance.rechunk import PreflightError

_SYMBOL = "ZZREB"

# Enough buckets that a single sub-window cannot cover the span by accident:
# the sweep must actually loop, or "it materialized everything" proves nothing
# about the windowing.
_SPAN = COVERAGE_BUCKET_INTERVAL * 12
_SUBWINDOW = COVERAGE_BUCKET_INTERVAL * 3

# A settled historical window, far from the policies' trailing edge, so a
# scheduled refresh cannot race the assertions.
_START = datetime(2015, 3, 2, tzinfo=UTC)


def _apply_migrations(url: str) -> None:
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)


def _pause_coverage_jobs(conn: psycopg.Connection[Any], view_name: str) -> None:
    """Pause the target's policies, as the runbook requires before a sweep."""
    conn.execute(
        "SELECT alter_job(job_id, scheduled => false) "
        "FROM timescaledb_information.jobs WHERE hypertable_name = %s",
        (view_name,),
    )


def _seed(url: str) -> None:
    """Seed raw bars across the full span, then empty both coverage caggs.

    Emptying is what makes this a *rebuild* test rather than a refresh test:
    migration 051 creates the caggs WITH NO DATA, so the state the sweep meets
    on production is exactly this one.
    """
    daily_rows = [
        (_START + COVERAGE_BUCKET_INTERVAL * i + timedelta(hours=12), _SYMBOL)
        for i in range(12)
    ]
    minute_rows = [
        (
            _START + COVERAGE_BUCKET_INTERVAL * i + timedelta(hours=14, minutes=31),
            _SYMBOL,
        )
        for i in range(12)
    ]

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instruments (canonical_id, symbol, asset_class, "
                " venue, trading_calendar_id, delisted_at_eodhd, eodhd_type, "
                " eodhd_exchange) VALUES (%s, %s, 'equity', 'US', 'NYSE', "
                " FALSE, 'Common Stock', 'US') ON CONFLICT DO NOTHING",
                (f"EQ:{_SYMBOL}", _SYMBOL),
            )
            for table, rows in (
                ("daily_ohlcv", daily_rows),
                ("minute_ohlcv", minute_rows),
            ):
                cur.executemany(
                    f"INSERT INTO {table} (time, symbol, open, high, low, close, "  # noqa: S608
                    "volume) VALUES (%s, %s, 10.0, 10.0, 10.0, 10.0, 100) "
                    "ON CONFLICT DO NOTHING",
                    rows,
                )
        conn.commit()

    with psycopg.connect(url, autocommit=True) as conn:
        # The 4-hour parent must carry the minute bars or minute_coverage rolls
        # up nothing (slice 167 s7): it is a hierarchical cagg.
        conn.execute(
            "CALL refresh_continuous_aggregate('"
            f"{GRANULARITY_SOURCE[Granularity.H4]}', NULL, NULL)"
        )
        # Empty both coverage caggs: reproduce migration 051's WITH NO DATA.
        for view in (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW):
            conn.execute(
                f"SELECT drop_chunks('{view}', "  # noqa: S608
                "older_than => '2100-01-01'::timestamptz)"
            )


@pytest.fixture
def swept_db(ephemeral_db: str) -> str:
    _apply_migrations(ephemeral_db)
    _seed(ephemeral_db)
    with psycopg.connect(ephemeral_db, autocommit=True) as conn:
        for view in (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW):
            _pause_coverage_jobs(conn, view)
    return ephemeral_db


def _connect(url: str) -> psycopg.Connection[dict[str, object]]:
    return psycopg.Connection.connect(url, autocommit=True, row_factory=dict_row)


def _rows(conn: psycopg.Connection[dict[str, object]], view: str) -> int:
    row = conn.execute(f"SELECT count(*) AS n FROM {view}").fetchone()  # noqa: S608
    return int(cast(int, row["n"])) if row else 0


class TestRebuildFromEmpty:
    """The production case: 051 leaves the cagg empty, the sweep fills it."""

    @pytest.mark.parametrize("family", [CoverageFamily.DAILY, CoverageFamily.MINUTE])
    def test_materializes_the_full_span(
        self, swept_db: str, family: CoverageFamily
    ) -> None:
        with _connect(swept_db) as conn:
            assert _rows(conn, _view(family)) == 0
            result = rebuild_coverage(conn, family, subwindow=_SUBWINDOW)

        assert result.windows > 1, (
            "the span must need more than one sub-window, or this proves "
            "nothing about the loop"
        )
        assert result.refreshed > 0
        assert result.rows_after > result.rows_before

        with _connect(swept_db) as conn:
            report = verify_coverage(conn, family)

        # The floor is what a head-only refresh would miss entirely.
        cov_lo = report["coverage_span"][0]
        src_lo = report["source_span"][0]
        assert cov_lo is not None
        assert cov_lo <= src_lo + COVERAGE_BUCKET_INTERVAL, (
            f"{family}: coverage starts at {cov_lo} but source starts at "
            f"{src_lo} — history was stranded"
        )

    def test_daily_covers_every_seeded_bucket(self, swept_db: str) -> None:
        """Row-level, not just span: a sweep that materialized only the first
        and last windows would still pass a span check."""
        with _connect(swept_db) as conn:
            rebuild_coverage(conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW)
            row = conn.execute(
                f"SELECT count(*) AS n FROM {DAILY_COVERAGE_VIEW}"  # noqa: S608
            ).fetchone()

        assert row is not None
        # 12 seeded bars, one per bucket, one symbol => 12 coverage rows.
        assert int(cast(int, row["n"])) == 12


class TestResume:
    """A kill costs one sub-window, not the run."""

    def test_second_run_skips_what_the_first_materialized(self, swept_db: str) -> None:
        with _connect(swept_db) as conn:
            first = rebuild_coverage(conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW)
            second = rebuild_coverage(conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW)

        assert first.refreshed > 0
        assert second.refreshed == 0, "a completed sweep must be a no-op on re-run"
        assert second.skipped == second.windows
        assert second.rows_after == first.rows_after, "re-run must not change rows"

    def test_force_rematerializes_regardless(self, swept_db: str) -> None:
        """--force is the escape hatch for a window suspected partial, where
        the content-derived skip would wrongly consider it done."""
        with _connect(swept_db) as conn:
            first = rebuild_coverage(conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW)
            forced = rebuild_coverage(
                conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW, force=True
            )

        assert forced.skipped == 0
        assert forced.refreshed == forced.windows
        # Idempotent: forcing changes nothing about the content.
        assert forced.rows_after == first.rows_after

    def test_partial_materialization_is_completed_by_a_re_run(
        self, swept_db: str
    ) -> None:
        """The interruption case, simulated: materialize part of the span, then
        let the sweep finish it."""
        with _connect(swept_db) as conn:
            conn.execute(
                "CALL refresh_continuous_aggregate(%s, %s::timestamptz, "
                "%s::timestamptz)",
                (DAILY_COVERAGE_VIEW, _START, _START + COVERAGE_BUCKET_INTERVAL * 3),
            )
            partial = _rows(conn, DAILY_COVERAGE_VIEW)
            assert 0 < partial < 12, "fixture must leave a genuinely partial cagg"

            result = rebuild_coverage(conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW)
            final = _rows(conn, DAILY_COVERAGE_VIEW)

        assert result.skipped > 0, "the already-materialized windows must be skipped"
        assert result.refreshed > 0, "the remaining windows must be refreshed"
        assert final == 12


class TestDryRun:
    def test_plans_without_mutating(self, swept_db: str) -> None:
        with _connect(swept_db) as conn:
            before = _rows(conn, DAILY_COVERAGE_VIEW)
            result = rebuild_coverage(
                conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW, dry_run=True
            )
            after = _rows(conn, DAILY_COVERAGE_VIEW)

        assert result.windows > 1
        assert after == before == 0

    def test_dry_run_skips_the_preflight(self, ephemeral_db: str) -> None:
        """--dry-run is read-only, so it must be safe to run at any time —
        including with the policies still live, which is when an operator
        actually wants to plan."""
        _apply_migrations(ephemeral_db)
        _seed(ephemeral_db)
        # Deliberately do NOT pause anything.
        with _connect(ephemeral_db) as conn:
            result = rebuild_coverage(
                conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW, dry_run=True
            )
        assert result.windows > 0


class TestPreflightAgainstARealCatalog:
    """The unit tier stubs the job catalog; this uses a real one."""

    def test_refuses_while_the_refresh_policy_is_live(self, ephemeral_db: str) -> None:
        _apply_migrations(ephemeral_db)
        _seed(ephemeral_db)
        # Policies installed by 052 and left scheduled.
        with _connect(ephemeral_db) as conn, pytest.raises(PreflightError) as excinfo:
            rebuild_coverage(conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW)

        message = str(excinfo.value)
        assert "alter_job" in message, "the refusal must print the fix"
        assert DAILY_COVERAGE_VIEW in message

    def test_minute_refuses_when_the_parent_refresh_is_paused(
        self, swept_db: str
    ) -> None:
        """minute_4hour_ohlcv is both this sweep's source and the daemon's
        coverage index. Pausing it makes the daemon re-seed every cycle."""
        parent = GRANULARITY_SOURCE[Granularity.H4]
        with _connect(swept_db) as conn:
            _pause_coverage_jobs(conn, parent)
            with pytest.raises(PreflightError, match=parent):
                rebuild_coverage(conn, CoverageFamily.MINUTE, subwindow=_SUBWINDOW)


class TestVerifyCoverage:
    def test_reports_parity_after_a_full_rebuild(self, swept_db: str) -> None:
        with _connect(swept_db) as conn:
            rebuild_coverage(conn, CoverageFamily.DAILY, subwindow=_SUBWINDOW)
            report = verify_coverage(conn, CoverageFamily.DAILY)

        assert report["coverage_symbols"] == report["source_symbols"]
        assert report["coverage_rows"] == 12
        assert report["head_within_one_bucket"] is True

    def test_detects_a_half_materialized_cagg(self, swept_db: str) -> None:
        """Catalog presence proves nothing: a cagg can be present, non-empty,
        and still missing most of its history (the 2026-08-04 lesson, and what
        slice 170's exit refresh found about the daily rollups).

        Materialize only the OLD end, so the cagg has rows but its head is far
        behind the source.
        """
        with _connect(swept_db) as conn:
            conn.execute(
                "CALL refresh_continuous_aggregate(%s, %s::timestamptz, "
                "%s::timestamptz)",
                (DAILY_COVERAGE_VIEW, _START, _START + COVERAGE_BUCKET_INTERVAL * 2),
            )
            report = verify_coverage(conn, CoverageFamily.DAILY)

        assert report["coverage_rows"] > 0, "cagg is present and non-empty..."
        assert report["head_within_one_bucket"] is False, (
            "...but its head is far behind the source, which is exactly what a "
            "presence check would miss"
        )


def _view(family: CoverageFamily) -> str:
    from manta_trading.market.maintenance.coverage_rebuild import FAMILY_VIEW

    return FAMILY_VIEW[family]
