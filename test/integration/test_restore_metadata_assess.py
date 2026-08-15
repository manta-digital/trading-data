"""Integration tests: ``restore_metadata.assess`` after migrations 051/052.

Slice 169 Task F. No test previously exercised ``restore_metadata`` at all
(verified: nothing imported it), which matters because this module is the
**incident-recovery path** — a stale reference here fails exactly when it is
least affordable.

**What this guards.** Slice 169's design predicted the module "references 046
as their creating migration [so] that reference must move to 051, or the
restore tool recreates them at the old width." That risk does not reproduce:
detection is by *catalog presence*, not by migration id (see the reconciliation
comment in ``assess``). These tests pin that mechanism, so if it were ever
changed to something migration-id-based — where the design's predicted staleness
WOULD bite — the change breaks a test instead of surfacing during an incident.

Runs against a throwaway database created by ``ephemeral_db``. Read-only: only
``assess()`` is called, never ``restore()``.
"""

from __future__ import annotations

import psycopg
import pytest

from manta_trading.constants import DAILY_COVERAGE_VIEW, MINUTE_COVERAGE_VIEW

_COVERAGE_VIEWS = (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW)


@pytest.fixture
def migrated(ephemeral_db: str) -> str:
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)
    return ephemeral_db


class TestAssessAfterSlice169:
    """F.1: both coverage caggs report present once 051/052 have applied."""

    def test_coverage_caggs_are_not_reported_missing(self, migrated: str) -> None:
        from manta_trading.data.quality.restore_metadata import assess

        with psycopg.connect(migrated) as conn:
            assessment = assess(conn)

        for view in _COVERAGE_VIEWS:
            assert view not in assessment.missing_caggs, (
                f"{view} reported missing on a fully-migrated database; "
                f"missing_caggs={assessment.missing_caggs}"
            )

    def test_no_migration_is_reported_missing(self, migrated: str) -> None:
        """051/052 are picked up automatically because ``missing_migrations``
        diffs the live ``MINUTE_MIGRATIONS`` list against the ledger — there is
        no hardcoded migration id to fall out of date."""
        from manta_trading.data.quality.restore_metadata import assess

        with psycopg.connect(migrated) as conn:
            assessment = assess(conn)

        assert assessment.missing_migrations == []

    def test_051_and_052_are_in_the_ledger(self, migrated: str) -> None:
        """The premise of the test above: they really did apply, so an empty
        ``missing_migrations`` means "nothing missing" rather than "nothing
        known about"."""
        with psycopg.connect(migrated) as conn:
            applied = {
                r[0]
                for r in conn.execute("SELECT migration_id FROM schema_migrations")
            }

        assert "051_coverage_cagg_bucket_narrowing" in applied
        assert "052_coverage_cagg_refresh_policies_narrowed" in applied

    def test_detection_is_by_catalog_presence_not_ledger(self, migrated: str) -> None:
        """The mechanism itself, pinned.

        Drop a coverage cagg while LEAVING its creating migrations in the
        ledger — the exact state the 2026-08-04 restore hit, and the state a
        migration-id-based check cannot see. ``assess`` must still report it
        missing.

        If this ever fails, detection has become ledger-based and the design's
        predicted staleness has become real.
        """
        from manta_trading.data.quality.restore_metadata import assess

        with psycopg.connect(migrated, autocommit=True) as conn:
            # data_status depends on both caggs (the 051 ordering rule).
            conn.execute("DROP VIEW IF EXISTS data_status")
            conn.execute(f"DROP MATERIALIZED VIEW {DAILY_COVERAGE_VIEW}")

            applied = {
                r[0]
                for r in conn.execute("SELECT migration_id FROM schema_migrations")
            }
            assert "046_create_coverage_caggs" in applied
            assert "051_coverage_cagg_bucket_narrowing" in applied

            assessment = assess(conn)

        assert DAILY_COVERAGE_VIEW in assessment.missing_caggs
        # ...and the ledger still looks complete, which is precisely why
        # catalog-presence detection is required.
        assert assessment.missing_migrations == []
