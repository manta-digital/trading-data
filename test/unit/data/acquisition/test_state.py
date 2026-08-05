"""
Tests for AcquisitionStateRepository (slimmed shape, slice 142).

Unit tests mock psycopg3 pool connections.
Integration tests (class TestAcquisitionStateRepositoryIntegration) require
MT_TIMESCALE_DB_URL and will skip cleanly when the variable is not set.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from manta_trading.data.acquisition.state import (
    AcquisitionStateRepository,
    AcquisitionStateRow,
    Granularity,
    LastAttemptOutcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_row(**overrides) -> AcquisitionStateRow:
    defaults = dict(
        symbol="AAPL",
        granularity=Granularity.DAILY,
        provider="eodhd",
        last_attempt_ts=_NOW,
        last_attempt_outcome=LastAttemptOutcome.SUCCESS,
        updated_at=_NOW,
    )
    return AcquisitionStateRow(**{**defaults, **overrides})


def _dict_for(row: AcquisitionStateRow) -> dict:
    """Convert AcquisitionStateRow to psycopg3 dict_row format."""
    return {
        "symbol": row.symbol,
        "granularity": str(row.granularity),
        "provider": row.provider,
        "last_attempt_ts": row.last_attempt_ts,
        "last_attempt_outcome": (
            str(row.last_attempt_outcome) if row.last_attempt_outcome else None
        ),
        "updated_at": row.updated_at,
    }


def _make_repo(pool_mock: MagicMock) -> AcquisitionStateRepository:
    return AcquisitionStateRepository(pool_mock)


def _stub_cursor(
    pool_mock: MagicMock,
    *,
    fetchone=None,
    fetchall=None,
    rowcount: int = 1,
) -> MagicMock:
    """Wire pool_mock to return provided values from a cursor."""
    cursor_mock = MagicMock()
    cursor_mock.fetchone.return_value = fetchone
    cursor_mock.fetchall.return_value = fetchall if fetchall is not None else []
    cursor_mock.rowcount = rowcount
    cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
    cursor_mock.__exit__ = MagicMock(return_value=False)

    conn_mock = MagicMock()
    conn_mock.cursor.return_value = cursor_mock
    conn_mock.__enter__ = MagicMock(return_value=conn_mock)
    conn_mock.__exit__ = MagicMock(return_value=False)

    pool_mock.connection.return_value = conn_mock
    return cursor_mock


# ---------------------------------------------------------------------------
# DTO shape — confirms slimmed schema (slice 142)
# ---------------------------------------------------------------------------


class TestAcquisitionStateRowShape:
    def test_no_old_fields(self):
        row = _make_row()
        # Removed in migration 019 (slice 142)
        assert not hasattr(row, "last_success_ts")
        assert not hasattr(row, "retry_count")
        assert not hasattr(row, "error_message")
        assert not hasattr(row, "run_id")
        assert not hasattr(row, "status")
        # Removed in migration 030 (slice 152, adjusted-on-read)
        assert not hasattr(row, "last_adjusted_ca_snapshot_id")

    def test_current_fields_present(self):
        row = _make_row()
        assert hasattr(row, "last_attempt_outcome")


# ---------------------------------------------------------------------------
# Unit tests (mocked pool)
# ---------------------------------------------------------------------------


class TestAcquisitionStateRepositoryUpsert:
    def test_upsert_calls_execute(self):
        pool = MagicMock()
        cursor = _stub_cursor(pool)
        repo = _make_repo(pool)
        row = _make_row()

        repo.upsert(row)

        cursor.execute.assert_called_once()
        sql, _params = cursor.execute.call_args[0]
        assert "acquisition_state" in sql
        assert "ON CONFLICT" in sql

    def test_upsert_passes_correct_params(self):
        pool = MagicMock()
        cursor = _stub_cursor(pool)
        repo = _make_repo(pool)
        row = _make_row(
            symbol="MSFT",
            granularity=Granularity.MINUTE,
            provider="eodhd",
            last_attempt_outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
        )

        repo.upsert(row)

        _, params = cursor.execute.call_args[0]
        # Order: symbol, granularity, provider, last_attempt_ts,
        #        last_attempt_outcome
        assert len(params) == 5
        assert params[0] == "MSFT"
        assert params[1] == "minute"
        assert params[2] == "eodhd"
        assert params[4] == "transient_failure"

    def test_upsert_passes_string_for_enum_params(self):
        """Enums must be serialized to strings, not enum objects, for psycopg3."""
        pool = MagicMock()
        cursor = _stub_cursor(pool)
        repo = _make_repo(pool)
        row = _make_row()

        repo.upsert(row)

        _, params = cursor.execute.call_args[0]
        # granularity (idx 1) must be plain string
        assert isinstance(params[1], str)
        # last_attempt_outcome (idx 4) must be plain string when set
        assert params[4] is None or isinstance(params[4], str)

    def test_upsert_handles_none_outcome(self):
        pool = MagicMock()
        cursor = _stub_cursor(pool)
        repo = _make_repo(pool)
        row = _make_row(last_attempt_outcome=None)

        repo.upsert(row)

        _, params = cursor.execute.call_args[0]
        assert params[4] is None


class TestAcquisitionStateRepositoryGet:
    def test_get_returns_row_on_hit(self):
        pool = MagicMock()
        row = _make_row()
        _stub_cursor(pool, fetchone=_dict_for(row))
        repo = _make_repo(pool)

        result = repo.get("AAPL", Granularity.DAILY, "eodhd")

        assert result is not None
        assert result.symbol == "AAPL"
        assert result.granularity is Granularity.DAILY
        assert result.provider == "eodhd"
        assert result.last_attempt_outcome is LastAttemptOutcome.SUCCESS

    def test_get_returns_none_on_miss(self):
        pool = MagicMock()
        _stub_cursor(pool, fetchone=None)
        repo = _make_repo(pool)

        result = repo.get("UNKNOWN", Granularity.DAILY, "eodhd")

        assert result is None

    def test_get_passes_correct_pk_params(self):
        pool = MagicMock()
        cursor = _stub_cursor(pool, fetchone=None)
        repo = _make_repo(pool)

        repo.get("TSLA", Granularity.MINUTE, "eodhd")

        _, params = cursor.execute.call_args[0]
        assert params == ("TSLA", "minute", "eodhd")

    def test_get_round_trips_nullable_fields(self):
        pool = MagicMock()
        row = _make_row(
            last_attempt_ts=None,
            last_attempt_outcome=None,
        )
        _stub_cursor(pool, fetchone=_dict_for(row))
        repo = _make_repo(pool)

        result = repo.get("AAPL", Granularity.DAILY, "eodhd")

        assert result is not None
        assert result.last_attempt_ts is None
        assert result.last_attempt_outcome is None


class TestAcquisitionStateRepositoryList:
    def test_list_no_filters_returns_all(self):
        pool = MagicMock()
        row1 = _make_row(symbol="AAPL")
        row2 = _make_row(
            symbol="MSFT",
            last_attempt_outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
        )
        _stub_cursor(pool, fetchall=[_dict_for(row1), _dict_for(row2)])
        repo = _make_repo(pool)

        result = repo.list()

        assert len(result) == 2
        assert result[0].symbol == "AAPL"
        assert result[1].symbol == "MSFT"

    def test_list_with_symbol_filter_passes_correct_params(self):
        pool = MagicMock()
        cursor = _stub_cursor(pool, fetchall=[])
        repo = _make_repo(pool)

        repo.list(symbol="AAPL")

        _, params = cursor.execute.call_args[0]
        assert params[0] == "AAPL"
        assert params[1] == "AAPL"

    def test_list_with_no_filters_passes_none_params(self):
        pool = MagicMock()
        cursor = _stub_cursor(pool, fetchall=[])
        repo = _make_repo(pool)

        repo.list()

        _, params = cursor.execute.call_args[0]
        # All filter params should be None when no filters given
        assert params[0] is None  # symbol
        assert params[2] is None  # granularity
        assert params[4] is None  # provider

    def test_list_empty_result(self):
        pool = MagicMock()
        _stub_cursor(pool, fetchall=[])
        repo = _make_repo(pool)

        result = repo.list()

        assert result == []

    def test_list_combined_filters(self):
        pool = MagicMock()
        cursor = _stub_cursor(pool, fetchall=[])
        repo = _make_repo(pool)

        repo.list(
            symbol="AAPL",
            granularity=Granularity.DAILY,
            provider="eodhd",
        )

        _, params = cursor.execute.call_args[0]
        assert params[0] == "AAPL"
        assert params[2] == "daily"
        assert params[4] == "eodhd"


# ---------------------------------------------------------------------------
# Coverage import is gone — import must fail (slice 142 deletion)
# ---------------------------------------------------------------------------


class TestCoveragePackageGone:
    def test_import_raises(self):
        with pytest.raises(ImportError):
            import manta_trading.data.coverage  # noqa: F401


# ---------------------------------------------------------------------------
# Integration tests (real DB, skips if MT_TIMESCALE_DB_URL not set)
# ---------------------------------------------------------------------------


@pytest.fixture
def acq_repo(migrated_db: str):
    """Repository on a fresh throwaway database.

    Previously wrote test_provider rows into whatever MT_TIMESCALE_DB_URL
    pointed at (2026-08-04 incident class). No cleanup DELETE needed: the
    database is created for the test and dropped after it.
    """
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(migrated_db, min_size=1, max_size=2)
    repo = AcquisitionStateRepository(pool)
    yield repo
    pool.close()


class TestAcquisitionStateRepositoryIntegration:
    """Integration tests against a real database.

    All rows use provider='test_provider' for easy cleanup.
    """

    def test_upsert_and_get_round_trip(self, acq_repo: AcquisitionStateRepository):
        row = AcquisitionStateRow(
            symbol="AAPL",
            granularity=Granularity.DAILY,
            provider="test_provider",
            last_attempt_ts=_NOW,
            last_attempt_outcome=LastAttemptOutcome.SUCCESS,
        )
        acq_repo.upsert(row)

        result = acq_repo.get("AAPL", Granularity.DAILY, "test_provider")

        assert result is not None
        assert result.symbol == "AAPL"
        assert result.granularity == Granularity.DAILY
        assert result.provider == "test_provider"
        assert result.last_attempt_outcome == LastAttemptOutcome.SUCCESS

    def test_upsert_updates_existing_row(self, acq_repo: AcquisitionStateRepository):
        row = AcquisitionStateRow(
            symbol="AAPL",
            granularity=Granularity.DAILY,
            provider="test_provider",
            last_attempt_ts=_NOW,
            last_attempt_outcome=LastAttemptOutcome.SUCCESS,
        )
        acq_repo.upsert(row)

        updated = AcquisitionStateRow(
            symbol="AAPL",
            granularity=Granularity.DAILY,
            provider="test_provider",
            last_attempt_ts=_NOW,
            last_attempt_outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
        )
        acq_repo.upsert(updated)

        result = acq_repo.get("AAPL", Granularity.DAILY, "test_provider")
        assert result is not None
        assert result.last_attempt_outcome == LastAttemptOutcome.TRANSIENT_FAILURE

        # Confirm only one row exists
        all_rows = acq_repo.list(symbol="AAPL", provider="test_provider")
        aapl_rows = [r for r in all_rows if r.symbol == "AAPL"]
        assert len(aapl_rows) == 1

    def test_get_returns_none_for_missing_pk(
        self, acq_repo: AcquisitionStateRepository
    ):
        result = acq_repo.get("ZZZZ_MISSING", Granularity.DAILY, "test_provider")
        assert result is None

    def test_list_no_filters_returns_seeded_rows(
        self, acq_repo: AcquisitionStateRepository
    ):
        for sym in ["AAPL", "MSFT"]:
            acq_repo.upsert(AcquisitionStateRow(
                symbol=sym,
                granularity=Granularity.DAILY,
                provider="test_provider",
                last_attempt_ts=_NOW,
                last_attempt_outcome=LastAttemptOutcome.SUCCESS,
            ))

        result = acq_repo.list(provider="test_provider")
        symbols = {r.symbol for r in result}
        assert {"AAPL", "MSFT"}.issubset(symbols)

    def test_list_combined_filters(self, acq_repo: AcquisitionStateRepository):
        for gran in [Granularity.DAILY, Granularity.MINUTE]:
            acq_repo.upsert(AcquisitionStateRow(
                symbol="AAPL",
                granularity=gran,
                provider="test_provider",
                last_attempt_ts=_NOW,
                last_attempt_outcome=LastAttemptOutcome.SUCCESS,
            ))

        result = acq_repo.list(
            symbol="AAPL",
            granularity=Granularity.MINUTE,
            provider="test_provider",
        )
        assert len(result) == 1
        assert result[0].granularity == Granularity.MINUTE
