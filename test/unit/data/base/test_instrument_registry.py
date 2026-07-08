"""
Unit tests for instrument_registry module.

Tests Instrument dataclass and InstrumentRegistry with mocked psycopg3 pool.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from manta_trading.data.base.instrument_registry import (
    Instrument,
    InstrumentRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_instrument(**overrides) -> Instrument:
    """Return a minimal valid Instrument, with optional field overrides."""
    defaults = dict(
        instrument_id=1,
        canonical_id="AAPL.NASDAQ",
        symbol="AAPL",
        asset_class="equity",
        venue="NASDAQ",
        currency="USD",
        tick_size=0.01,
        lot_size=1,
        trading_calendar_id="NASDAQ",
        adjustment_policy="split_adjusted",
        metadata=None,
        delisted_at_eodhd=False,
    )
    return Instrument(**{**defaults, **overrides})


def _row_for(inst: Instrument) -> dict:
    """Convert an Instrument to a dict row (as psycopg3 dict_row returns)."""
    return dict(
        instrument_id=inst.instrument_id,
        canonical_id=inst.canonical_id,
        symbol=inst.symbol,
        asset_class=inst.asset_class,
        venue=inst.venue,
        currency=inst.currency,
        tick_size=inst.tick_size,
        lot_size=inst.lot_size,
        trading_calendar_id=inst.trading_calendar_id,
        adjustment_policy=inst.adjustment_policy,
        metadata=inst.metadata,
        delisted_at_eodhd=inst.delisted_at_eodhd,
    )


def _make_registry(pool_mock: MagicMock) -> InstrumentRegistry:
    """Construct InstrumentRegistry with a mocked pool."""
    with patch("manta_trading.data.base.instrument_registry.ConnectionPool", return_value=pool_mock):
        return InstrumentRegistry("postgresql://fake/db")


def _stub_cursor(pool_mock: MagicMock, fetchone=None, fetchall=None, rowcount=1):
    """Wire pool_mock so that cursor queries return provided values."""
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
# TestInstrument — dataclass tests (keep as-is)
# ---------------------------------------------------------------------------

class TestInstrument:
    """Tests for Instrument dataclass."""

    def test_instantiation(self):
        inst = _make_instrument()
        assert inst.instrument_id == 1
        assert inst.canonical_id == "AAPL.NASDAQ"
        assert inst.symbol == "AAPL"
        assert inst.asset_class == "equity"
        assert inst.venue == "NASDAQ"

    def test_defaults(self):
        inst = Instrument(
            instrument_id=1,
            canonical_id="TEST.NYSE",
            symbol="TEST",
            asset_class="equity",
            venue="NYSE",
        )
        assert inst.currency == "USD"
        assert inst.lot_size == 1
        assert inst.adjustment_policy == "split_adjusted"
        assert inst.delisted_at_eodhd is False
        assert inst.tick_size is None
        assert inst.metadata is None


# ---------------------------------------------------------------------------
# TestInstrumentRegistryLookups — Tasks 2 & 3
# ---------------------------------------------------------------------------

class TestInstrumentRegistryLookups:
    """Tests for lookup methods: get_by_symbol, get_by_canonical_id, get_by_provider_symbol."""

    # --- get_by_symbol ---

    def test_get_by_symbol_returns_instrument_on_hit(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        _stub_cursor(pool, fetchone=_row_for(inst))

        result = registry.get_by_symbol("AAPL")
        assert result == inst

    def test_get_by_symbol_returns_none_on_miss(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        _stub_cursor(pool, fetchone=None)

        result = registry.get_by_symbol("UNKNOWN")
        assert result is None

    def test_get_by_symbol_uses_cache_on_second_call(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        cursor = _stub_cursor(pool, fetchone=_row_for(inst))

        registry.get_by_symbol("AAPL")
        registry.get_by_symbol("AAPL")

        # DB should only be queried once
        assert cursor.execute.call_count == 1

    # --- get_instrument (alias for get_by_symbol) ---

    def test_get_instrument_returns_same_as_get_by_symbol(self):
        """get_instrument delegates to get_by_symbol and returns the same Instrument."""
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        _stub_cursor(pool, fetchone=_row_for(inst))

        result = registry.get_instrument("AAPL")
        assert result == inst

    def test_get_instrument_returns_none_on_miss(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        _stub_cursor(pool, fetchone=None)

        result = registry.get_instrument("UNKNOWN")
        assert result is None

    # --- get_by_canonical_id ---

    def test_get_by_canonical_id_returns_instrument_on_hit(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        _stub_cursor(pool, fetchone=_row_for(inst))

        result = registry.get_by_canonical_id("AAPL.NASDAQ")
        assert result == inst

    def test_get_by_canonical_id_returns_none_on_miss(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        _stub_cursor(pool, fetchone=None)

        result = registry.get_by_canonical_id("NOPE.NYSE")
        assert result is None

    # --- get_by_provider_symbol ---

    def test_get_by_provider_symbol_returns_instrument_on_hit(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        _stub_cursor(pool, fetchone=_row_for(inst))

        result = registry.get_by_provider_symbol("alphavantage", "AAPL")
        assert result == inst

    def test_get_by_provider_symbol_returns_none_on_miss(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        _stub_cursor(pool, fetchone=None)

        result = registry.get_by_provider_symbol("alphavantage", "UNKNOWN")
        assert result is None

    def test_get_by_provider_symbol_uses_today_when_date_not_given(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        cursor = _stub_cursor(pool, fetchone=_row_for(inst))

        registry.get_by_provider_symbol("alphavantage", "AAPL")

        # Verify date.today() was passed as params (positions 2 and 3)
        call_args = cursor.execute.call_args
        params = call_args[0][1]
        assert params[2] == date.today()
        assert params[3] == date.today()

    # --- cache invalidation ---

    def test_invalidate_cache_causes_db_query_on_next_call(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        cursor = _stub_cursor(pool, fetchone=_row_for(inst))

        registry.get_by_symbol("AAPL")
        registry._invalidate_cache()
        registry.get_by_symbol("AAPL")

        assert cursor.execute.call_count == 2


# ---------------------------------------------------------------------------
# TestInstrumentRegistryWrites — Tasks 4 & 5
# ---------------------------------------------------------------------------

class TestInstrumentRegistryWrites:
    """Tests for write methods: register_instrument, update_provider_mapping, list_instruments."""

    # --- register_instrument ---

    def test_register_instrument_returns_instrument_and_true_on_insert(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        _stub_cursor(pool, fetchone=_row_for(inst))

        result, was_inserted = registry.register_instrument(
            canonical_id="AAPL.NASDAQ",
            symbol="AAPL",
            asset_class="equity",
            venue="NASDAQ",
        )
        assert result == inst
        assert was_inserted is True

    def test_register_instrument_on_conflict_falls_back_to_get_by_canonical_id(self):
        """When RETURNING yields no row (conflict), should fetch existing and return False."""
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()

        # First call (INSERT RETURNING): no row (conflict triggered)
        # Second call (get_by_canonical_id): returns existing
        cursor = _stub_cursor(pool, fetchone=None)
        cursor.fetchone.side_effect = [None, _row_for(inst)]

        result, was_inserted = registry.register_instrument(
            canonical_id="AAPL.NASDAQ",
            symbol="AAPL",
            asset_class="equity",
            venue="NASDAQ",
        )
        assert result == inst
        assert was_inserted is False

    def test_register_instrument_calls_invalidate_cache(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        _stub_cursor(pool, fetchone=_row_for(inst))

        # Populate cache first
        registry._cache["symbol:AAPL"] = inst
        assert len(registry._cache) == 1

        registry.register_instrument(
            canonical_id="AAPL.NASDAQ",
            symbol="AAPL",
            asset_class="equity",
            venue="NASDAQ",
        )
        assert len(registry._cache) == 0

    # --- update_provider_mapping ---

    def test_update_provider_mapping_calls_insert(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        cursor = _stub_cursor(pool)

        registry.update_provider_mapping(42, "alphavantage", "AAPL")

        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert "provider_symbol_mapping" in sql
        assert params == (42, "alphavantage", "AAPL")

    def test_update_provider_mapping_calls_invalidate_cache(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        inst = _make_instrument()
        _stub_cursor(pool)

        # Populate cache
        registry._cache["canonical:AAPL.NASDAQ"] = inst

        registry.update_provider_mapping(1, "alphavantage", "AAPL")
        assert len(registry._cache) == 0

    # --- list_instruments ---

    def test_list_instruments_no_filters_returns_all(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        row1 = _row_for(_make_instrument(instrument_id=1, symbol="AAPL", canonical_id="AAPL.NASDAQ"))
        row2 = _row_for(_make_instrument(instrument_id=2, symbol="IBM", canonical_id="IBM.NYSE", venue="NYSE"))
        _stub_cursor(pool, fetchall=[row1, row2])

        result = registry.list_instruments()
        assert len(result) == 2
        assert result[0].symbol == "AAPL"
        assert result[1].symbol == "IBM"

    def test_list_instruments_with_venue_filter(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        cursor = _stub_cursor(pool, fetchall=[])

        registry.list_instruments(venue="NYSE")

        call_args = cursor.execute.call_args
        params = call_args[0][1]
        # venue params are positions 2 and 3
        assert params[2] == "NYSE"
        assert params[3] == "NYSE"

    def test_list_instruments_with_asset_class_filter(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        cursor = _stub_cursor(pool, fetchall=[])

        registry.list_instruments(asset_class="etf")

        params = cursor.execute.call_args[0][1]
        assert params[0] == "etf"
        assert params[1] == "etf"

    def test_list_instruments_active_only_false(self):
        pool = MagicMock()
        registry = _make_registry(pool)
        cursor = _stub_cursor(pool, fetchall=[])

        registry.list_instruments(active_only=False)

        params = cursor.execute.call_args[0][1]
        # active_only=False → NOT False = True at param position 4
        assert params[4] is False

    # --- close ---

    def test_close_calls_pool_close(self):
        pool = MagicMock()
        registry = _make_registry(pool)

        registry.close()

        pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# TestInstrumentLifecycleFields — Task 3.3 (slice 141)
# ---------------------------------------------------------------------------

class TestInstrumentLifecycleFields:
    """Tests for lifecycle fields added in slice 141."""

    def test_instrument_accepts_new_fields(self):
        inst = Instrument(
            instrument_id=1,
            canonical_id="AAPL.NASDAQ",
            symbol="AAPL",
            asset_class="equity",
            venue="NASDAQ",
            first_listing_date=date(1980, 12, 12),
            first_data_date=date(2000, 1, 3),
            delisted_date=None,
            eodhd_type="Common Stock",
            eodhd_exchange="US",
            delisted_at_eodhd=False,
        )
        assert inst.first_listing_date == date(1980, 12, 12)
        assert inst.eodhd_type == "Common Stock"
        assert inst.delisted_at_eodhd is False

    def test_instrument_lifecycle_field_defaults(self):
        inst = Instrument(
            instrument_id=1,
            canonical_id="TEST.NYSE",
            symbol="TEST",
            asset_class="equity",
            venue="NYSE",
        )
        assert inst.first_listing_date is None
        assert inst.first_data_date is None
        assert inst.delisted_date is None
        assert inst.eodhd_type is None
        assert inst.eodhd_exchange is None
        assert inst.delisted_at_eodhd is False

    def test_instrument_cols_includes_lifecycle_fields(self):
        from manta_trading.data.base.instrument_registry import _INSTRUMENT_COLS
        for col in (
            "first_listing_date",
            "first_data_date",
            "delisted_date",
            "eodhd_type",
            "eodhd_exchange",
            "delisted_at_eodhd",
        ):
            assert col in _INSTRUMENT_COLS, f"_INSTRUMENT_COLS missing: {col}"
