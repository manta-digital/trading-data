"""Unit tests for slice 128 minute-track migrations 012/013/014.

These tests verify structural properties of the migration SQL plus the
``CoverageGapStatus`` enum that is shared between the migration file and
the runtime scanner persister. They do not connect to a database; the
end-to-end "applies cleanly" assertion is covered by
``test/integration/test_coverage_persistence.py`` (Phase 4).
"""

from __future__ import annotations

import pytest

from manta_trading.market.schema.migrations.minute import (
    MINUTE_MIGRATIONS,
    _COVERAGE_GAP_STATUS_PROVIDER_CONFIRMED_UNFILLABLE,
    _COVERAGE_GAP_STATUS_UNKNOWN,
    _coverage_status_check_sql,
)


def _migration(mid: str) -> dict[str, str]:
    for m in MINUTE_MIGRATIONS:
        if m["id"] == mid:
            return m
    raise AssertionError(f"migration {mid!r} not found")


_EXPECTED_COVERAGE_STATUS_VALUES = {
    "unknown",
    "provider_confirmed_unfillable",
    "retry_pending",
    "resolved",
}


class TestCoverageGapStatusEnum:
    def test_expected_values(self) -> None:
        # Values are inlined in migrations/minute.py after coverage/ package deletion
        from manta_trading.market.schema.migrations.minute import _COVERAGE_STATUS_SORTED
        assert set(_COVERAGE_STATUS_SORTED) == _EXPECTED_COVERAGE_STATUS_VALUES

    def test_check_sql_lists_every_value_quoted(self) -> None:
        sql = _coverage_status_check_sql()
        for val in _EXPECTED_COVERAGE_STATUS_VALUES:
            assert f"'{val}'" in sql
        assert sql.startswith("resolution_status IN (")

    def test_check_sql_is_deterministic(self) -> None:
        assert _coverage_status_check_sql() == _coverage_status_check_sql()


class TestMigration012CoverageGaps:
    def setup_method(self) -> None:
        self.migration = _migration("012_coverage_gaps")

    def test_creates_table_idempotently(self) -> None:
        sql = self.migration["sql"]
        assert "CREATE TABLE IF NOT EXISTS coverage_gaps" in sql

    def test_primary_key_on_symbol_gap_start_source(self) -> None:
        assert "PRIMARY KEY (symbol, gap_start, source)" in self.migration["sql"]

    def test_check_constraint_present(self) -> None:
        sql = self.migration["sql"]
        assert "coverage_gaps_resolution_status_check" in sql
        for val in _EXPECTED_COVERAGE_STATUS_VALUES:
            assert sql.count(f"'{val}'") >= 1

    def test_range_check_constraint_present(self) -> None:
        assert "gap_end >= gap_start" in self.migration["sql"]

    def test_indexes_on_symbol_and_status(self) -> None:
        sql = self.migration["sql"]
        assert "idx_coverage_gaps_symbol" in sql
        assert "idx_coverage_gaps_status" in sql


class TestMigration013BackfillState:
    def setup_method(self) -> None:
        self.migration = _migration("013_backfill_state")

    def test_creates_table_idempotently(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS backfill_state" in self.migration["sql"]

    def test_primary_key_on_universe(self) -> None:
        assert "universe                  TEXT PRIMARY KEY" in self.migration["sql"]

    def test_columns_for_quota_tracking(self) -> None:
        sql = self.migration["sql"]
        assert "daily_calls_used" in sql
        assert "daily_calls_window_start" in sql
        assert "cursor_symbol" in sql
        assert "since_date" in sql


class TestMigration014NvdaInauguralRow:
    def setup_method(self) -> None:
        self.migration = _migration("014_nvda_inaugural_gap")

    def test_inserts_nvda_row(self) -> None:
        sql = self.migration["sql"]
        assert "INSERT INTO coverage_gaps" in sql
        assert "'NVDA'" in sql
        assert "'2024-06-07T23:59:00Z'" in sql
        assert "'2024-07-25T08:00:00Z'" in sql
        assert "'eodhd'" in sql

    def test_idempotent_via_on_conflict(self) -> None:
        # Re-running the migration framework against an already-seeded DB
        # must not fail; ON CONFLICT DO NOTHING is the contract.
        assert "ON CONFLICT (symbol, gap_start, source) DO NOTHING" in (
            self.migration["sql"]
        )

    def test_uses_provider_confirmed_unfillable_status_via_constant(self) -> None:
        # The literal in the SQL must equal the inlined constant so a future
        # value change surfaces as a test failure rather than a silent mismatch.
        expected = _COVERAGE_GAP_STATUS_PROVIDER_CONFIRMED_UNFILLABLE
        assert f"'{expected}'" in self.migration["sql"]


class TestMigrationOrdering:
    def test_012_013_014_in_order(self) -> None:
        ids = [m["id"] for m in MINUTE_MIGRATIONS]
        assert "012_coverage_gaps" in ids
        assert "013_backfill_state" in ids
        assert "014_nvda_inaugural_gap" in ids
        assert (
            ids.index("012_coverage_gaps")
            < ids.index("013_backfill_state")
            < ids.index("014_nvda_inaugural_gap")
        )

    def test_012_precedes_014(self) -> None:
        # 014 inserts into the table 012 creates; 012 must run first.
        ids = [m["id"] for m in MINUTE_MIGRATIONS]
        assert ids.index("012_coverage_gaps") < ids.index("014_nvda_inaugural_gap")


class TestParametrizedMigrationStructure:
    @pytest.mark.parametrize(
        "mid",
        ["012_coverage_gaps", "013_backfill_state", "014_nvda_inaugural_gap"],
    )
    def test_required_keys(self, mid: str) -> None:
        m = _migration(mid)
        assert m["id"] == mid
        assert m["description"]
        assert m["sql"].strip()
